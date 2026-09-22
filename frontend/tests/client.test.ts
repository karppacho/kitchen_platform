import { http, HttpResponse } from 'msw'
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
