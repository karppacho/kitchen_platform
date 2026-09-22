import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { App } from '../src/App'
import { setViewport } from './setup'

// С задачи 8 App проверяет сессию через /api/me, поэтому каркасному тесту
// нужны QueryClientProvider (его требует SessionProvider) и Router (его
// требуют защищённые маршруты). Сама проверка не про вход — просто про то,
// что страница рисуется и на ней есть заголовок.
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

test('приложение рисуется', async () => {
  server.use(http.get('/api/me', () => new HttpResponse(null, { status: 401 })))

  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole('heading')).toBeInTheDocument()
})

test('подмена ширины работает', () => {
  setViewport(360)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(false)
  setViewport(1440)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(true)
})

test('setViewport меняет window.innerWidth', () => {
  setViewport(360)
  expect(window.innerWidth).toBe(360)
  setViewport(1440)
  expect(window.innerWidth).toBe(1440)
})

test('подписчик matchMedia через addEventListener получает matches в change-событии', () => {
  // Так подписываются реальные компоненты (канонический паттерн useWide):
  // mql.addEventListener('change', (e) => setWide(e.matches)). Проверяем
  // именно этот путь, а не чтение matches напрямую, — иначе тест не видит
  // дыру, в которой подписчику вместо matches прилетает undefined.
  const mql = window.matchMedia('(min-width: 1080px)')
  let shirokiy = mql.matches
  mql.addEventListener('change', (e) => {
    shirokiy = (e as MediaQueryListEvent).matches
  })

  setViewport(360)
  expect(shirokiy).toBe(false)

  setViewport(1440)
  expect(shirokiy).toBe(true)
})
