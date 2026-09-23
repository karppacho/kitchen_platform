import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  const vid = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Dishes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
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
  // Подпись видна текстом: всплывающий title на телефоне не показывается.
  const stroka = screen.getByText('Салат овощной').closest('tr')!
  expect(stroka).toHaveTextContent(/проверьте единицы измерения/i)
  expect(screen.queryByText(/убыток/i)).not.toBeInTheDocument()
})

test('ноль замечаний не тревожит, а больше нуля — заметен', async () => {
  // Смотрим саму ячейку замечаний, а не всю строку: в строке есть «B001»,
  // и проверка «где-то есть единица» прошла бы при любом числе.
  server.use(
    http.get('/api/dishes', () => HttpResponse.json([{ ...BLYUDA[0]!, warnings: 0 }, BLYUDA[2]!])),
  )
  narisovat()
  const bez = (await screen.findByText('Круасан с мортаделой')).closest('tr')!
  expect(bez.lastElementChild).toHaveTextContent(/^0$/)
  expect(bez.querySelector('.zamechaniya')).toBeNull()

  const s = screen.getByText('Салат овощной').closest('tr')!
  expect(s.querySelector('.zamechaniya')).toHaveTextContent(/^2$/)
})

test('на 360 px остаются название, себестоимость и маржа', async () => {
  setViewport(360)
  narisovat()
  await screen.findByText('Круасан с мортаделой')

  expect(screen.getByText('84,66 ₽')).toBeInTheDocument()
  expect(screen.getByText('77,1 %')).toBeInTheDocument()
  expect(screen.queryByText('369,00 ₽')).not.toBeInTheDocument()
})

test('сбой фонового обновления не стирает показанное', async () => {
  // Вернулся во вкладку на кухонном Wi-Fi — обновление упало. Прежние данные
  // верны, пока не пришли новые; стирать их — отнять экран из-за связи.
  const { queries } = narisovat()
  await screen.findByText('Круасан с мортаделой')
  server.use(http.get('/api/dishes', () => HttpResponse.error()))
  await act(() => queries.refetchQueries())
  expect(await screen.findByText(/не удалось обновить/i)).toBeInTheDocument()
  expect(screen.getByText('Круасан с мортаделой')).toBeInTheDocument()
})

test('статус, выбранный пока поиск ждёт паузы, не пропадает', async () => {
  // Поиск уходит в адрес через 300 мс после последней буквы. Если за это
  // время выбрать статус, отложенная запись поиска не должна его стереть.
  narisovat()
  await screen.findByText('Круасан с мортаделой')
  fireEvent.change(screen.getByLabelText('Поиск по названию'), { target: { value: 'кр' } })
  fireEvent.change(screen.getByLabelText('Статус'), { target: { value: 'активное' } })
  await act(() => new Promise((r) => setTimeout(r, 400)))
  // Ждём ответа на новый поиск: пока он идёт, вариантов статуса в списке
  // нет, и выпадающий список не может показать выбранный.
  await waitFor(() => expect(screen.getByLabelText('Статус')).toHaveValue('активное'))
})
