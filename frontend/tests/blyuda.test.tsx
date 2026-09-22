import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Dishes } from '../src/pages/Dishes'
import { setViewport } from './setup'

// Ответы настоящие: B001 с ценой, B003 из тех четырнадцати, у кого её нет,
// и выдуманная строка, где себестоимость выше цены.
const BLYUDA = [
  {
    legacy_id: 'B001',
    name: 'Круасан с мортаделой',
    category: 'Блюдо',
    status: 'активное',
    price_menu: '369.00',
    uc_rub: '84.66',
    uc_percent: '22.9',
    margin_percent: '77.1',
    output_grams: '72.000',
    warnings: 1,
  },
  {
    legacy_id: 'B003',
    name: 'Кетчуп',
    category: 'Соус-топпинг',
    status: 'активное',
    price_menu: null,
    uc_rub: '5.52',
    uc_percent: null,
    margin_percent: null,
    output_grams: '25.000',
    warnings: 1,
  },
  {
    legacy_id: 'B099',
    name: 'Салат овощной',
    category: 'Блюдо',
    status: 'активное',
    price_menu: '280.00',
    uc_rub: '1398.00',
    uc_percent: '499.3',
    margin_percent: '-399.3',
    output_grams: '210.000',
    warnings: 2,
  },
]

const server = setupServer(http.get('/api/dishes', () => HttpResponse.json(BLYUDA)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Dishes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('проценты показываются числом с запятой', async () => {
  narisovat()
  await screen.findByText('Круасан с мортаделой')
  expect(screen.getByText('22,9 %')).toBeInTheDocument()
})

test('у блюда без цены меню маржа — прочерк, а не ноль', async () => {
  narisovat()
  await screen.findByText('Кетчуп')
  const stroka = screen.getByText('Кетчуп').closest('tr')!
  expect(stroka.textContent).not.toMatch(/0\s*%/)
  expect(stroka.querySelectorAll('.num--pusto').length).toBeGreaterThanOrEqual(3)
})

test('себестоимость выше цены помечена красным', async () => {
  narisovat()
  await screen.findByText('Салат овощной')
  expect(screen.getByText('Салат овощной').closest('tr')).toHaveClass('stroka--ubytok')
})

test('подпись направляет к причине, а не пугает', async () => {
  // Это почти всегда перепутанная единица измерения, а не убыток.
  narisovat()
  await screen.findByText('Салат овощной')
  const podskazka = screen.getByTitle(/проверьте единицы измерения/i)
  expect(podskazka).toBeInTheDocument()
  expect(screen.queryByText(/убыток/i)).not.toBeInTheDocument()
})

test('ноль замечаний не тревожит, а больше нуля — заметен', async () => {
  narisovat()
  await screen.findByText('Круасан с мортаделой')
  expect(screen.getByText('Круасан с мортаделой').closest('tr')!.textContent).toContain('1')
})

test('на 360 px остаются название, себестоимость и маржа', async () => {
  setViewport(360)
  narisovat()
  await screen.findByText('Круасан с мортаделой')

  expect(screen.getByText('84,66 ₽')).toBeInTheDocument()
  expect(screen.getByText('77,1 %')).toBeInTheDocument()
  expect(screen.queryByText('369,00 ₽')).not.toBeInTheDocument()
})
