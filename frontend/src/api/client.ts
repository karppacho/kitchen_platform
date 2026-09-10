export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Идущее продление. Общее на все запросы: три запроса при открытии
 *  экрана не должны давать три продления, из которых два отвергнутся
 *  вращением refresh-токена. */
let prodlenie: Promise<boolean> | null = null

function prodlit(): Promise<boolean> {
  prodlenie ??= fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
    .then((otvet) => otvet.ok)
    .catch(() => false)
    .finally(() => {
      prodlenie = null
    })
  return prodlenie
}

async function poyasnenie(otvet: Response): Promise<string> {
  try {
    const telo = (await otvet.json()) as { detail?: unknown }
    if (typeof telo.detail === 'string') return telo.detail
  } catch {
    // Тело не JSON — бывает у 502 от nginx. Это не повод падать.
  }
  return 'Не удалось получить данные'
}

/**
 * Запрос к API.
 *
 * 401 лечится однократным продлением и повтором. Один раз, не в цикле:
 * при протухшем refresh-токене цикл крутился бы вечно и выглядел бы как
 * зависание. 403 продлением не лечится и уходит наверх как есть.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const zapros = (): Promise<Response> =>
    fetch(`/api${path}`, { ...init, credentials: 'include' })

  let otvet: Response
  try {
    otvet = await zapros()
  } catch {
    throw new ApiError(0, 'Нет связи с сервером')
  }

  if (otvet.status === 401 && (await prodlit())) {
    try {
      otvet = await zapros()
    } catch {
      throw new ApiError(0, 'Нет связи с сервером')
    }
  }

  if (!otvet.ok) {
    throw new ApiError(otvet.status, await poyasnenie(otvet))
  }
  if (otvet.status === 204) {
    return undefined as T
  }
  return (await otvet.json()) as T
}
