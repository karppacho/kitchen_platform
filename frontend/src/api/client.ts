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
 *  - `sboy` — refresh недоступен (5xx или обрыв сети: бэкенд просто не
 *    ответил) либо отказал 403 (защита от подделки, отключённая учётка).
 *    Это не значит, что сессия мертва. Путать этот исход с `otkaz`
 *    значит выбрасывать на форму входа всех, у кого истёк access-токен,
 *    при каждом перезапуске бэкенда, хотя refresh-токен у них ещё жив.
 *    `soobshchenie` — что показать человеку. */
type IshodProdleniya =
  | { itog: 'ok' }
  | { itog: 'otkaz' }
  | { itog: 'sboy'; status: number; soobshchenie: string }

/** Текст отказа защиты от подделки — тот же, что отдаёт сервер. */
const OTKAZ_ZASHCHITY = 'Запрос отклонён — обновите страницу'

/** Заголовок против подделки запросов. Сервер отклоняет изменяющий запрос
 *  с кукой сессии без него: форма на чужом сайте поставить его не может, а
 *  скрипт с чужой страницы — только с разрешения сервера, которого тот не
 *  даёт. Шлём и на вход: в браузере может лежать просроченная кука. */
const ZASHCHITA = 'X-Kitchen-Csrf'

/** Методы, которые что-то меняют на сервере, — те же, что проверяет сервер. */
const IZMENYAYUSHCHIE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/** Идущее продление. Общее на все запросы: три запроса при открытии
 *  экрана не должны давать три продления, из которых два отвергнутся
 *  вращением refresh-токена. Все ждущие получают один и тот же исход. */
let prodlenie: Promise<IshodProdleniya> | null = null

function prodlit(): Promise<IshodProdleniya> {
  prodlenie ??= fetch('/api/auth/refresh', {
    method: 'POST',
    credentials: 'include',
    headers: { [ZASHCHITA]: '1' },
  })
    .then(async (otvet): Promise<IshodProdleniya> => {
      if (otvet.ok) return { itog: 'ok' }
      if (otvet.status === 401) return { itog: 'otkaz' }
      if (otvet.status === 403) {
        // Чаще всего это защита от подделки: вкладка открыта со старым
        // кодом, который продлевает без заголовка. Лечится обновлением
        // страницы — так и говорим. Но 403 бывает и у отключённой учётки,
        // поэтому текст сервера, если он есть, важнее нашего.
        const soobshchenie = await poyasnenie(otvet, OTKAZ_ZASHCHITY)
        return { itog: 'sboy', status: 403, soobshchenie }
      }
      return { itog: 'sboy', status: otvet.status, soobshchenie: 'Не удалось получить данные' }
    })
    .catch((): IshodProdleniya => ({ itog: 'sboy', status: 0, soobshchenie: 'Нет связи с сервером' }))
    .finally(() => {
      // Сбрасываем независимо от исхода: следующий запрос обязан суметь
      // продлиться заново, а не унаследовать чужой сбой навсегда.
      prodlenie = null
    })
  return prodlenie
}

async function poyasnenie(
  otvet: Response,
  zapas = 'Не удалось получить данные',
): Promise<string> {
  try {
    const telo = (await otvet.json()) as { detail?: unknown }
    if (typeof telo.detail === 'string') return telo.detail
  } catch {
    // Тело не JSON — бывает у 502 от nginx. Это не повод падать.
  }
  return zapas
}

/**
 * Запрос к API.
 *
 * 401 лечится однократным продлением и повтором. Один раз, не в цикле:
 * при протухшем refresh-токене цикл крутился бы вечно и выглядел бы как
 * зависание. 403 продлением не лечится и уходит наверх как есть.
 *
 * Сбой самого продления (5xx, обрыв сети, 403) — это не «сессия мертва»:
 * наверх уходит ошибка со статусом продления или нулевым, но не 401, чтобы
 * экран не отправил человека на форму входа зря.
 *
 * Ручки `/auth/*` продление не запускают: они сами и есть вход/продление,
 * а login и refresh делят одну зону ограничения частоты nginx. Если
 * неверный пароль запускает продление, несколько подряд неверных попыток
 * посадят refresh на 503 — шеф увидит «Не удалось получить данные» вместо
 * «Неверная почта или пароль», а при живой refresh-куке пароль ушёл бы на
 * сервер дважды за один клик.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Свои заголовки вызывающего сохраняем: вход, например, шлёт Content-Type.
  const zagolovki = new Headers(init.headers)
  if (IZMENYAYUSHCHIE.has((init.method ?? 'GET').toUpperCase())) zagolovki.set(ZASHCHITA, '1')
  const zapros = (): Promise<Response> =>
    fetch(`/api${path}`, { ...init, headers: zagolovki, credentials: 'include' })

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
      throw new ApiError(ishod.status, ishod.soobshchenie)
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
