export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Исход продления.
 *  - `ok` — кука обновилась, исходный запрос можно повторить;
 *  - `otkaz` — refresh сам ответил 401: сессия действительно мертва,
 *    сюда и только сюда уместна отправка на вход;
 *  - `sboy` — refresh недоступен (5xx или обрыв сети). Это не значит, что
 *    сессия мертва: бэкенд просто не ответил. Путать этот исход с `otkaz`
 *    значит выбрасывать на форму входа всех, у кого истёк access-токен,
 *    при каждом перезапуске бэкенда, хотя refresh-токен у них ещё жив. */
type IshodProdleniya = { itog: 'ok' } | { itog: 'otkaz' } | { itog: 'sboy'; status: number }

/** Идущее продление. Общее на все запросы: три запроса при открытии
 *  экрана не должны давать три продления, из которых два отвергнутся
 *  вращением refresh-токена. Все ждущие получают один и тот же исход. */
let prodlenie: Promise<IshodProdleniya> | null = null

function prodlit(): Promise<IshodProdleniya> {
  prodlenie ??= fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
    .then((otvet): IshodProdleniya => {
      if (otvet.ok) return { itog: 'ok' }
      if (otvet.status === 401) return { itog: 'otkaz' }
      return { itog: 'sboy', status: otvet.status }
    })
    .catch((): IshodProdleniya => ({ itog: 'sboy', status: 0 }))
    .finally(() => {
      // Сбрасываем независимо от исхода: следующий запрос обязан суметь
      // продлиться заново, а не унаследовать чужой сбой навсегда.
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
 *
 * Сбой самого продления (5xx, обрыв сети) — это не «сессия мертва»: наверх
 * уходит ошибка с исходным или нулевым статусом, но не 401, чтобы экран не
 * отправил человека на форму входа зря.
 *
 * Ручки `/auth/*` продление не запускают: они сами и есть вход/продление,
 * а login и refresh делят одну зону ограничения частоты nginx. Если
 * неверный пароль запускает продление, несколько подряд неверных попыток
 * посадят refresh на 503 — шеф увидит «Не удалось получить данные» вместо
 * «Неверная почта или пароль», а при живой refresh-куке пароль ушёл бы на
 * сервер дважды за один клик.
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

  if (otvet.status === 401 && !path.startsWith('/auth/')) {
    const ishod = await prodlit()
    if (ishod.itog === 'ok') {
      try {
        otvet = await zapros()
      } catch {
        throw new ApiError(0, 'Нет связи с сервером')
      }
    } else if (ishod.itog === 'sboy') {
      throw new ApiError(
        ishod.status,
        ishod.status === 0 ? 'Нет связи с сервером' : 'Не удалось получить данные',
      )
    }
    // itog === 'otkaz' — падаем дальше на общую обработку !otvet.ok:
    // otvet всё ещё хранит исходный 401, и его тело идёт в сообщение.
  }

  if (!otvet.ok) {
    throw new ApiError(otvet.status, await poyasnenie(otvet))
  }
  if (otvet.status === 204) {
    return undefined as T
  }
  try {
    return (await otvet.json()) as T
  } catch {
    throw new ApiError(otvet.status, 'Не удалось получить данные')
  }
}
