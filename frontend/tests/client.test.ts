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

test('обрыв сети даёт понятную ошибку, а не пустой экран', async () => {
  server.use(http.get('/api/dishes', () => HttpResponse.error()))

  await expect(api('/dishes')).rejects.toMatchObject({ status: 0 })
})
