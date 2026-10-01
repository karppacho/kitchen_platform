import { delay, http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, test, vi } from 'vitest'

import { ApiError, api } from '../src/api/client'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

test('401 приводит к продлению и повтору запроса', async () => {
  let dano = false
  const prodleniya = vi.fn()

  server.use(
    http.get('/api/dishes', () => {
      if (!dano) return new HttpResponse(null, { status: 401 })
      return HttpResponse.json([{ legacy_id: 'B001' }])
    }),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      dano = true
      return HttpResponse.json({ email: 'chef@example.com' })
    }),
  )

  const otvet = await api<{ legacy_id: string }[]>('/dishes')

  expect(prodleniya).toHaveBeenCalledTimes(1)
  expect(otvet[0]!.legacy_id).toBe('B001')
})

test('второй 401 подряд не крутит цикл, а признаёт поражение', async () => {
  // Цикл «401 → продление → 401 → продление» при протухшем refresh-токене
  // крутился бы вечно и выглядел бы как зависание.
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return new HttpResponse(null, { status: 401 })
    }),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 401 })
  expect(prodleniya).toHaveBeenCalledTimes(1)
})

test('успешный refresh, но исходный запрос снова 401 — ровно один повтор, не цикл', async () => {
  // Кука могла не лечь (путь, secure) или пользователя удалили в GoTrue —
  // refresh отвечает 200, но /dishes всё равно 401. Здесь тоже не должно
  // начаться зацикливание: один повтор, и дальше — отказ. Прежде это
  // ловилось только тестом с refresh=401, который не отличил бы регрессию
  // на «while» от одиночного «if».
  let zaprosov = 0
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () => {
      zaprosov += 1
      return new HttpResponse(null, { status: 401 })
    }),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 401 })

  expect(prodleniya).toHaveBeenCalledTimes(1)
  expect(zaprosov).toBe(2)
})

test('параллельные 401 дают одно продление, а не три', async () => {
  // Три запроса при открытии экрана не должны давать три продления, из
  // которых два отвергнутся вращением refresh-токена.
  let dano = false
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () =>
      dano ? HttpResponse.json([]) : new HttpResponse(null, { status: 401 }),
    ),
    http.get('/api/ingredients', () =>
      dano ? HttpResponse.json([]) : new HttpResponse(null, { status: 401 }),
    ),
    http.get('/api/me', () =>
      dano ? HttpResponse.json({}) : new HttpResponse(null, { status: 401 }),
    ),
    http.post('/api/auth/refresh', async () => {
      prodleniya()
      dano = true
      return HttpResponse.json({})
    }),
  )

  await Promise.all([api('/dishes'), api('/ingredients'), api('/me')])

  expect(prodleniya).toHaveBeenCalledTimes(1)
})

test('403 продлением не лечится и наверх идёт как есть', async () => {
  // 401 — «представьтесь», 403 — «представились, но нельзя». Отправлять на
  // форму входа при 403 значит гонять человека по кругу.
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () =>
      HttpResponse.json({ detail: 'нужна роль: chef' }, { status: 403 }),
    ),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/dishes')).rejects.toBeInstanceOf(ApiError)
  await expect(api('/dishes')).rejects.toMatchObject({
    status: 403,
    message: 'нужна роль: chef',
  })
  expect(prodleniya).not.toHaveBeenCalled()
})

test('сбой самого продления не выдаётся за смерть сессии', async () => {
  // Refresh, упавший по сети, — не то же самое, что явный отказ 401: сессия
  // жива, сервис входа просто сейчас недоступен. Раньше .catch(() => false)
  // сворачивал оба случая в одно и то же — исходный 401 уходил наверх, и
  // экран отправил бы живого пользователя на форму входа.
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => HttpResponse.error()),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 0 })
})

test('после сбоя продления следующий запрос продлевается заново', async () => {
  // Общий промис продления не должен залипать в состоянии сбоя: finally
  // обязан сбросить его при любом исходе, иначе все последующие запросы
  // наследовали бы чужую сетевую ошибку навсегда.
  let popytka = 0
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      popytka += 1
      if (popytka === 1) return HttpResponse.error()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 0 })
  await expect(api('/dishes')).rejects.toMatchObject({ status: 401 })

  expect(prodleniya).toHaveBeenCalledTimes(2)
})

test('обрыв сети даёт понятную ошибку, а не пустой экран', async () => {
  server.use(http.get('/api/dishes', () => HttpResponse.error()))

  await expect(api('/dishes')).rejects.toMatchObject({ status: 0 })
})

test('502 с нечитаемым телом даёт понятную ошибку и не трогает продление', async () => {
  // if (otvet.status >= 500) return [] as T — молчаливо пустой экран,
  // который спека запрещает поимённо. 502 обязан дойти до экрана как
  // ошибка, а не как «справочник пуст».
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () => new HttpResponse('<html>Bad Gateway</html>', { status: 502 })),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/dishes')).rejects.toMatchObject({
    status: 502,
    message: 'Не удалось получить данные',
  })
  expect(prodleniya).not.toHaveBeenCalled()
})

test('login 401 не запускает продление', async () => {
  // /auth/login и /auth/refresh делят одну зону ограничения частоты nginx
  // (10 в минуту, всплеск 5). Если неверный пароль запускает продление,
  // несколько подряд неверных попыток посадят refresh на 503, и шеф увидит
  // «Не удалось получить данные» вместо «Неверная почта или пароль» — то
  // самое смешение отказа и недоступности, против которого построено всё
  // разделение кодов.
  const prodleniya = vi.fn()
  server.use(
    http.post('/api/auth/login', () =>
      HttpResponse.json({ detail: 'Неверная почта или пароль' }, { status: 401 }),
    ),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(
    api('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'chef@example.com', password: 'не тот' }),
    }),
  ).rejects.toMatchObject({ status: 401, message: 'Неверная почта или пароль' })

  expect(prodleniya).toHaveBeenCalledTimes(0)
})

test.each(['POST', 'PUT', 'PATCH', 'DELETE'])(
  '%s несёт заголовок против подделки запросов',
  async (metod) => {
    // Без него сервер отвечает на изменяющий запрос с кукой сессии 403.
    let zagolovok: string | null = null
    server.use(
      http.all('/api/primer', ({ request }) => {
        zagolovok = request.headers.get('X-Kitchen-Csrf')
        return new HttpResponse(null, { status: 204 })
      }),
    )

    await api('/primer', { method: metod })

    expect(zagolovok).toBe('1')
  },
)

test('GET заголовка против подделки не несёт', async () => {
  let zagolovok: string | null = 'ещё не спрашивали'
  server.use(
    http.get('/api/dishes', ({ request }) => {
      zagolovok = request.headers.get('X-Kitchen-Csrf')
      return HttpResponse.json([])
    }),
  )

  await api('/dishes')

  expect(zagolovok).toBeNull()
})

test('вход несёт и заголовок против подделки, и свой Content-Type', async () => {
  // В браузере может лежать просроченная кука сессии — тогда вход без
  // заголовка сервер отклонил бы. А свой Content-Type вызывающего не должен
  // потеряться при добавлении нашего заголовка.
  let zagolovki: Headers | null = null
  server.use(
    http.post('/api/auth/login', ({ request }) => {
      zagolovki = request.headers
      return HttpResponse.json({ email: 'chef@example.com' })
    }),
  )

  await api('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'chef@example.com', password: 'пароль' }),
  })

  expect(zagolovki!.get('X-Kitchen-Csrf')).toBe('1')
  expect(zagolovki!.get('Content-Type')).toBe('application/json')
})

test('продление несёт заголовок против подделки запросов', async () => {
  // Продление едет с кукой продления — без заголовка сервер ответил бы 403,
  // и каждый истёкший доступ выкидывал бы человека на форму входа.
  let dano = false
  let zagolovok: string | null = null
  server.use(
    http.get('/api/dishes', () =>
      dano ? HttpResponse.json([]) : new HttpResponse(null, { status: 401 }),
    ),
    http.post('/api/auth/refresh', ({ request }) => {
      zagolovok = request.headers.get('X-Kitchen-Csrf')
      dano = true
      return HttpResponse.json({})
    }),
  )

  await api('/dishes')

  expect(zagolovok).toBe('1')
})

test('повтор после продления тоже несёт заголовок против подделки', async () => {
  // PATCH → 401 → продление → повтор. Без заголовка на повторе сервер
  // ответил бы 403, и правка терялась бы ровно тогда, когда истёк доступ.
  let dano = false
  const zagolovki: (string | null)[] = []
  server.use(
    http.patch('/api/primer', ({ request }) => {
      zagolovki.push(request.headers.get('X-Kitchen-Csrf'))
      return new HttpResponse(null, { status: dano ? 204 : 401 })
    }),
    http.post('/api/auth/refresh', () => {
      dano = true
      return HttpResponse.json({})
    }),
  )

  await api('/primer', { method: 'PATCH' })

  expect(zagolovki).toEqual(['1', '1'])
})

const OTKAZ_ZASHCHITY = 'Запрос отклонён — обновите страницу'

test('отказ защиты на самом запросе доходит до экрана текстом сервера', async () => {
  const prodleniya = vi.fn()
  server.use(
    http.patch('/api/primer', () =>
      HttpResponse.json({ detail: OTKAZ_ZASHCHITY }, { status: 403 }),
    ),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/primer', { method: 'PATCH' })).rejects.toMatchObject({
    status: 403,
    message: OTKAZ_ZASHCHITY,
  })
  expect(prodleniya).not.toHaveBeenCalled()
})

test('отказ защиты на продлении показывает «обновите страницу», а не «не удалось»', async () => {
  // Так бывает на вкладке, открытой со старым кодом до выкладки: продление
  // уходит без заголовка. «Не удалось получить данные» не подсказало бы,
  // что делать, — а сделать нужно ровно одно: обновить страницу.
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () =>
      HttpResponse.json({ detail: OTKAZ_ZASHCHITY }, { status: 403 }),
    ),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 403, message: OTKAZ_ZASHCHITY })
})

test('403 продления без тела — тоже «обновите страницу»', async () => {
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 403 })),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 403, message: OTKAZ_ZASHCHITY })
})

test('403 продления со своим текстом показывает текст сервера', async () => {
  // Продление отвечает 403 и когда учётку отключили. «Обновите страницу»
  // тут отправило бы человека по кругу — нужен текст сервера.
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () =>
      HttpResponse.json({ detail: 'доступ отключён' }, { status: 403 }),
    ),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 403, message: 'доступ отключён' })
})

test('200 с нечитаемым телом даёт ApiError, а не голый SyntaxError', async () => {
  // Экраны различают ошибки по ApiError.status. Необработанный SyntaxError
  // для них вообще не ошибка API — упадёт мимо любого catch на этот тип.
  server.use(http.get('/api/dishes', () => new HttpResponse('не json', { status: 200 })))

  await expect(api('/dishes')).rejects.toBeInstanceOf(ApiError)
  await expect(api('/dishes')).rejects.toMatchObject({
    status: 200,
    message: 'Не удалось получить данные',
  })
})

test('отказ несёт поле, строку и чего не хватает — экрану, а не только текст', async () => {
  server.use(
    http.patch('/api/cards/drafts/1', () =>
      HttpResponse.json({ detail: 'Белки: «abc» — не число', field: 'protein' }, { status: 422 }),
    ),
    http.post('/api/cards/drafts/1/submit', () =>
      HttpResponse.json({ detail: '«Томаты» уже есть в таблице — строка 3.', row: 3 }, { status: 409 }),
    ),
    http.post('/api/cards/drafts/2/submit', () =>
      HttpResponse.json(
        { detail: 'Чтобы отправить карточку, заполните: Поставщик', missing: ['Поставщик'] },
        { status: 422 },
      ),
    ),
  )

  await expect(api('/cards/drafts/1', { method: 'PATCH' })).rejects.toMatchObject({
    status: 422,
    message: 'Белки: «abc» — не число',
    field: 'protein',
  })
  await expect(api('/cards/drafts/1/submit', { method: 'POST' })).rejects.toMatchObject({
    status: 409,
    row: 3,
  })
  await expect(api('/cards/drafts/2/submit', { method: 'POST' })).rejects.toMatchObject({
    status: 422,
    missing: ['Поставщик'],
  })
})

test('отказ без поля — поля пустые, а не мусор из тела', async () => {
  server.use(
    http.post('/api/cards/recognize/1', () =>
      HttpResponse.json({ detail: 'Этикетка уже распознаётся — подождите', field: 7 }, { status: 409 }),
    ),
  )

  const oshibka = await api('/cards/recognize/1', { method: 'POST' }).catch((e: unknown) => e)

  expect(oshibka).toBeInstanceOf(ApiError)
  expect(oshibka).toMatchObject({ field: null, row: null, missing: null })
})

test.each([
  [429, 'Слишком часто — подождите минуту'],
  [413, 'Фото больше 8 МБ — сфотографируйте ещё раз'],
])('%s от nginx без JSON — свой текст по коду', async (kod, tekst) => {
  server.use(
    http.put(
      '/api/cards/drafts/1/photos/label',
      () =>
        new HttpResponse('<html><body>nginx</body></html>', {
          status: kod,
          headers: { 'Content-Type': 'text/html' },
        }),
    ),
  )

  await expect(api('/cards/drafts/1/photos/label', { method: 'PUT' })).rejects.toMatchObject({
    status: kod,
    message: tekst,
  })
})

test('429 приложения — его текст, а не общий', async () => {
  // Дневной бюджет и лимит повара приходят с тем же кодом, что частота nginx,
  // но со своим объяснением — оно важнее.
  const tekst = 'Вы сегодня распознали уже 40 этикеток — это предел на день. Заполните поля вручную.'
  server.use(
    http.post('/api/cards/recognize/1', () => HttpResponse.json({ detail: tekst }, { status: 429 })),
  )

  await expect(api('/cards/recognize/1', { method: 'POST' })).rejects.toMatchObject({
    status: 429,
    message: tekst,
  })
})

test('запрос со сроком: сервер не ответил вовремя — ошибка связи, а не вечное ожидание', async () => {
  server.use(
    http.get('/api/cards/drafts/current', async () => {
      await delay(1000)
      return HttpResponse.json(null)
    }),
  )

  await expect(api('/cards/drafts/current', { srok: 50 })).rejects.toMatchObject({
    status: 0,
    message: 'Сервер не ответил вовремя — проверьте связь',
  })
})

test('срок не мешает запросу, ответившему вовремя', async () => {
  server.use(http.get('/api/cards/options', () => HttpResponse.json({ categories: [], suppliers: [] })))

  await expect(api('/cards/options', { srok: 5000 })).resolves.toEqual({
    categories: [],
    suppliers: [],
  })
})

test('отмена вызывающим прерывает запрос', async () => {
  server.use(
    http.get('/api/cards/name-check', async () => {
      await delay(1000)
      return HttpResponse.json({})
    }),
  )
  const otmena = new AbortController()
  const zapros = api('/cards/name-check?name=x', { signal: otmena.signal, srok: 5000 })
  otmena.abort()

  await expect(zapros).rejects.toMatchObject({ status: 0 })
})

test('срок покрывает и продление сессии: зависшее продление не держит запрос дольше срока', async () => {
  // Токен истёк, а продление застряло в сети. Без срока на него кнопка
  // оставалась бы серой, пока телефон ловит сеть, сколько бы ни было в srok.
  let otpustit: () => void = () => {}
  const prodlenieZhdyot = new Promise<void>((gotovo) => {
    otpustit = gotovo
  })
  server.use(
    http.get('/api/cards/drafts/current', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', async () => {
      await prodlenieZhdyot
      return new HttpResponse(null, { status: 401 })
    }),
  )

  try {
    await expect(api('/cards/drafts/current', { srok: 50 })).rejects.toMatchObject({
      status: 0,
      message: 'Сервер не ответил вовремя — проверьте связь',
    })
  } finally {
    // Продление общее на все запросы — отпускаем, чтобы следующий тест
    // не унаследовал зависшее.
    otpustit()
    await api('/auth/refresh', { method: 'POST' }).catch(() => undefined)
  }
})
