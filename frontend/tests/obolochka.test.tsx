import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { App } from '../src/App'
import { RAZDELY } from '../src/shell/razdely'
import { setViewport } from './setup'

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
  ),
  http.get('/api/dishes', () => HttpResponse.json([])),
  http.get('/api/ingredients', () => HttpResponse.json([])),
  http.get('/api/reconciliation', () =>
    HttpResponse.json({ total: 0, linked: 0, needs_human: 0, rows: [] }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat(putj = '/dishes') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[putj]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('в меню видны все восемь разделов, включая будущие', async () => {
  // Оболочка проектируется один раз и должна знать про все разделы —
  // иначе меню придётся переделывать при появлении каждого следующего.
  narisovat()
  await screen.findByText('Алексей')

  expect(RAZDELY).toHaveLength(8)
  for (const razdel of RAZDELY) {
    expect(screen.getByRole('link', { name: razdel.nazvanie })).toBeInTheDocument()
  }
})

test('заглушка открывается и говорит, когда раздел появится', async () => {
  narisovat()
  await screen.findByText('Алексей')

  await userEvent.click(screen.getByRole('link', { name: 'Дегустации' }))

  expect(screen.getByRole('heading', { name: 'Дегустации' })).toBeInTheDocument()
  expect(screen.getByText(/фаза 4/i)).toBeInTheDocument()
})

test('выход есть и он в шапке', async () => {
  narisovat()
  await screen.findByText('Алексей')

  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
})

test('пункт «Блюда» остаётся текущим и на карточке блюда', async () => {
  narisovat('/dishes/B001')
  await screen.findByText('Алексей')

  expect(screen.getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', { name: 'Карточка блюда' })).toBeInTheDocument()
})

test('неизвестный адрес не даёт пустой экран', async () => {
  narisovat('/net-takogo-razdela')
  await screen.findByText('Алексей')

  // Уводит на главный раздел, а не оставляет пустоту: шапка и заголовок
  // раздела на месте.
  expect(screen.getByRole('heading', { name: 'Блюда' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
})

test('на узком экране меню открывается кнопкой и закрывается после выбора пункта', async () => {
  setViewport(360)
  const { container } = narisovat()
  await screen.findByText('Алексей')

  const knopka = screen.getByRole('button', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  // Кнопка — обычный <button>, поэтому таб-порядок и активация с клавиатуры
  // (Enter/Space) даёт браузер сам, без ручной обвязки.
  expect(knopka.tagName).toBe('BUTTON')

  await userEvent.click(knopka)
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(container.querySelector('.bok')).toHaveClass('bok--otkryt')

  await userEvent.click(screen.getByRole('link', { name: 'Справочник' }))

  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(container.querySelector('.bok')).not.toHaveClass('bok--otkryt')
})
