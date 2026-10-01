/** Что, кроме текста, сервер кладёт в отказ — для экрана: у отказа правки —
 *  поле, которое подсветить; у дубля карточки — строку листа; у неполного
 *  черновика — чего не хватает (названиями для повара). */
export type PodrobnostiOtkaza = {
  field?: string | null
  row?: number | null
  missing?: readonly string[] | null
}

export class ApiError extends Error {
  readonly field: string | null
  readonly row: number | null
  readonly missing: readonly string[] | null

  constructor(
    readonly status: number,
    message: string,
    podrobnosti: PodrobnostiOtkaza = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.field = podrobnosti.field ?? null
    this.row = podrobnosti.row ?? null
    this.missing = podrobnosti.missing ?? null
  }
}

const NET_SVYAZI = 'Нет связи с сервером'
const NE_DOZHDALIS = 'Сервер не ответил вовремя — проверьте связь'

/** Текст отказа, у которого нет своего текста: тело не JSON (страница
 *  nginx, сбой сервера до ответа). Экран может заменить его своим. */
export const NE_POLUCHILOS = 'Не удалось получить данные'

/** Частота запросов nginx — ждать минуту, а не до завтра: этим он
 *  отличается от дневного лимита распознавания с тем же кодом 429. Тот же
 *  текст nginx шлёт и сам, в JSON. */
export const SLISHKOM_CHASTO = 'Слишком часто — подождите минуту'

/** Свой текст на отказы nginx, у которых тело — его страница, а не JSON.
 *  С круга 1 задачи 8 nginx отвечает и на них JSON, но старый конфиг или
 *  чужой прокси по дороге могут вернуть страницу. */
const TEKST_PO_KODU: Readonly<Record<number, string>> = {
  413: 'Фото больше 8 МБ — сфотографируйте ещё раз',
  429: SLISHKOM_CHASTO,
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
        const soobshchenie = (await razobratOtkaz(otvet, OTKAZ_ZASHCHITY)).tekst
        return { itog: 'sboy', status: 403, soobshchenie }
      }
      return { itog: 'sboy', status: otvet.status, soobshchenie: NE_POLUCHILOS }
    })
    .catch((): IshodProdleniya => ({ itog: 'sboy', status: 0, soobshchenie: NET_SVYAZI }))
    .finally(() => {
      // Сбрасываем независимо от исхода: следующий запрос обязан суметь
      // продлиться заново, а не унаследовать чужой сбой навсегда.
      prodlenie = null
    })
  return prodlenie
}

type Otkaz = { tekst: string; podrobnosti: PodrobnostiOtkaza }

async function razobratOtkaz(otvet: Response, zapas = NE_POLUCHILOS): Promise<Otkaz> {
  try {
    const telo = (await otvet.json()) as {
      detail?: unknown
      field?: unknown
      row?: unknown
      missing?: unknown
    }
    if (typeof telo.detail === 'string') {
      const { field, row, missing } = telo
      return {
        tekst: telo.detail,
        podrobnosti: {
          field: typeof field === 'string' ? field : null,
          row: typeof row === 'number' ? row : null,
          missing:
            Array.isArray(missing) && missing.every((m) => typeof m === 'string') ? missing : null,
        },
      }
    }
  } catch {
    // Тело не JSON — бывает у 502 от nginx. Это не повод падать.
  }
  return { tekst: TEKST_PO_KODU[otvet.status] ?? zapas, podrobnosti: {} }
}

/**
 * Сигнал запроса: отмена вызывающим и истёкший срок — в одном.
 *
 * Своими руками, без AbortSignal.any и AbortSignal.timeout: первого нет в
 * Safari до 17.4, второго — до 16, а у поваров телефоны разные.
 */
function storozh(signal: AbortSignal | null | undefined, srok: number | undefined) {
  const ostanovka = new AbortController()
  let istyok = false
  const otmenit = () => ostanovka.abort()
  if (signal?.aborted) ostanovka.abort()
  else signal?.addEventListener('abort', otmenit, { once: true })
  const taimer =
    srok === undefined
      ? undefined
      : setTimeout(() => {
          istyok = true
          ostanovka.abort()
        }, srok)
  const oshibkaSvyazi = () => new ApiError(0, istyok ? NE_DOZHDALIS : NET_SVYAZI)
  return {
    signal: ostanovka.signal,
    /** Ошибка связи: обрыв — «нет связи», вышел срок — «не ответил вовремя». */
    oshibkaSvyazi,
    /**
     * Дождаться общего обещания, но не дольше срока и не после отмены.
     * Само обещание не обрывается — его ждут и другие запросы.
     */
    dozhdatsya: <T,>(obeshchanie: Promise<T>): Promise<T> =>
      new Promise<T>((gotovo, otkaz) => {
        const stop = () => otkaz(oshibkaSvyazi())
        if (ostanovka.signal.aborted) {
          stop()
          return
        }
        ostanovka.signal.addEventListener('abort', stop, { once: true })
        obeshchanie.then(gotovo, otkaz).finally(() => {
          ostanovka.signal.removeEventListener('abort', stop)
        })
      }),
    snyat: () => {
      clearTimeout(taimer)
      signal?.removeEventListener('abort', otmenit)
    },
  }
}

export type Zapros = RequestInit & {
  /** Сколько ждать ответа целиком, мс. Не дождались — ошибка связи
   *  (status 0): кнопка не должна оставаться серой без срока, пока телефон
   *  ловит сеть в подвале. */
  srok?: number
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
 *
 * `srok` — сколько ждать ответа; `signal` — отмена вызывающим. И то и
 * другое обрывает запрос и даёт ошибку связи (status 0).
 */
export async function api<T>(path: string, { srok, signal, ...init }: Zapros = {}): Promise<T> {
  // Свои заголовки вызывающего сохраняем: вход, например, шлёт Content-Type.
  const zagolovki = new Headers(init.headers)
  if (IZMENYAYUSHCHIE.has((init.method ?? 'GET').toUpperCase())) zagolovki.set(ZASHCHITA, '1')
  const strazh = storozh(signal, srok)
  const zapros = async (): Promise<Response> => {
    try {
      return await fetch(`/api${path}`, {
        ...init,
        signal: strazh.signal,
        headers: zagolovki,
        credentials: 'include',
      })
    } catch {
      throw strazh.oshibkaSvyazi()
    }
  }

  try {
    let otvet = await zapros()

    if (otvet.status === 401 && !path.startsWith('/auth/')) {
      // Срок — на весь запрос, с продлением: застрявшее в сети продление
      // иначе держало бы кнопку серой сколько угодно.
      const ishod = await strazh.dozhdatsya(prodlit())
      if (ishod.itog === 'ok') {
        otvet = await zapros()
      } else if (ishod.itog === 'sboy') {
        throw new ApiError(ishod.status, ishod.soobshchenie)
      }
      // itog === 'otkaz' — падаем дальше на общую обработку !otvet.ok:
      // otvet всё ещё хранит исходный 401, и его тело идёт в сообщение.
    }

    if (!otvet.ok) {
      const { tekst, podrobnosti } = await razobratOtkaz(otvet)
      throw new ApiError(otvet.status, tekst, podrobnosti)
    }
    if (otvet.status === 204) {
      return undefined as T
    }
    try {
      return (await otvet.json()) as T
    } catch {
      // Тело оборвалось на полпути — это связь, а не нечитаемый ответ.
      if (strazh.signal.aborted) throw strazh.oshibkaSvyazi()
      throw new ApiError(otvet.status, NE_POLUCHILOS)
    }
  } finally {
    strazh.snyat()
  }
}
