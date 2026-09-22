import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Ingredients } from '../src/pages/Ingredients'
import { setViewport } from './setup'

// Ответ настоящий: тот самый случай с двумя «Сахарами», из-за которого
// сверка не может решить сама.
// id и legacy_id намеренно различаются: тест «показывает legacy_id, а не
// внутренний» иначе не мог бы отличить одно от другого — при совпадающих
// значениях он прошёл бы и при ошибочном показе внутреннего id.
const SAHAR = [
  {
    id: 9001,
    legacy_id: '12',
    name: 'Сахар',
    category: 'Бакалея',
    unit: 'кг',
    status: 'активный',
    price_per_kg: '100.00',
    weight_per_piece_g: null,
    has_card: false,
  },
  {
    id: 9002,
    legacy_id: '123',
    name: 'Сахар',
    category: 'Бакалея',
    unit: 'шт',
    status: 'архивный',
    price_per_kg: '0.00',
    weight_per_piece_g: null,
    has_card: true,
  },
]

const server = setupServer(http.get('/api/ingredients', () => HttpResponse.json(SAHAR)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Ingredients />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('показывает legacy_id, а не внутренний', async () => {
  // Шеф знает в лицо идентификатор из таблицы. Внутренний ему не говорит
  // ничего и только путает. id и legacy_id в фикстуре разные специально —
  // иначе тест не отличил бы верный показ от ошибочного.
  narisovat()
  expect(await screen.findAllByText('Сахар')).toHaveLength(2)
  expect(screen.getByText('12')).toBeInTheDocument()
  expect(screen.getByText('123')).toBeInTheDocument()
  expect(screen.queryByText('9001')).not.toBeInTheDocument()
  expect(screen.queryByText('9002')).not.toBeInTheDocument()
})

test('единица определяет смысл цены', async () => {
  // price_per_kg при unit «шт» означает цену за штуку, несмотря на имя поля.
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getByText('100 ₽/кг')).toBeInTheDocument()
  expect(screen.getByText('0 ₽/шт')).toBeInTheDocument()
})

test('вес штуки у весового — прочерк, а не ноль', async () => {
  // Не «сколько прочерков на странице» (length > 0 прошёл бы и тогда,
  // когда прочерк оказался не в той колонке): берём именно строку
  // весового ингредиента (unit «кг», legacy_id «12») и проверяем ячейку
  // веса штуки внутри неё.
  narisovat()
  await screen.findAllByText('Сахар')

  const stroka = screen.getByText('12').closest('tr')
  expect(stroka).not.toBeNull()
  expect(within(stroka as HTMLElement).getByLabelText('значения нет')).toBeInTheDocument()
})

test('архивные не прячутся, а помечаются', async () => {
  // Они стоят в составе живых блюд, и без них не разобрать, почему у
  // блюда такая себестоимость.
  //
  // Текст «архивный» встречается на странице дважды — ещё и как пункт
  // селекта статуса, — поэтому берём именно строку архивного ингредиента
  // (legacy_id «123») и ищем статус внутри неё, а не по всей странице.
  narisovat()
  await screen.findAllByText('Сахар')

  const stroka = screen.getByText('123').closest('tr')
  expect(stroka).not.toBeNull()
  expect(within(stroka as HTMLElement).getByText('архивный')).toBeInTheDocument()
})

test('отсутствие карточки — видимый сигнал', async () => {
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getByLabelText('карточки нет')).toBeInTheDocument()
  expect(screen.getByLabelText('карточка есть')).toBeInTheDocument()
})

test('на 360 px остаются имя, цена и отметка карточки', async () => {
  setViewport(360)
  narisovat()
  await screen.findAllByText('Сахар')

  expect(screen.getByText('100 ₽/кг')).toBeInTheDocument()
  expect(screen.queryByText('Бакалея')).not.toBeInTheDocument()
})

test('статус живёт в адресе и фильтруется на клиенте, не сужая список статусов', async () => {
  // Открыли присланную ссылку с уже выбранным статусом — экран сразу
  // показывает только архивные, без повторного клика по фильтру.
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/?status=архивный']}>
        <Ingredients />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  await screen.findAllByText('Сахар')
  expect(screen.getAllByText('Сахар')).toHaveLength(1)
  expect(screen.getByText('123')).toBeInTheDocument()
  expect(screen.queryByText('12')).not.toBeInTheDocument()

  // Список статусов в выпадающем списке остаётся полным: выбор «архивный»
  // не должен стирать «активный» из вариантов — иначе вернуться к нему
  // можно было бы только сбросом фильтра.
  expect(screen.getByRole('option', { name: 'активный' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'архивный' })).toBeInTheDocument()
})
