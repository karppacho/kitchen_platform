import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Reconciliation } from '../src/pages/Reconciliation'

// Ответ настоящий, из раздела 4 ТЗ. Данные грязные — «Оснвова» с опечаткой,
// лишний пробел, поставщик «Хз» — и показываются как есть.
const SVERKA = {
  total: 101,
  linked: 85,
  needs_human: 16,
  rows: [
    {
      card_id: 7,
      name: 'Булочка для датского хот дога',
      link_status: 'ambiguous',
      supplier: 'Хз',
      candidates: [
        { ingredient_id: 34, legacy_id: '34', name: 'Булочка для датского хот дога', score: null },
        { ingredient_id: 121, legacy_id: '121', name: 'Булочка для датского хот дога', score: null },
      ],
    },
    {
      card_id: 25,
      name: 'Корж для римской пиццы',
      link_status: 'candidate',
      supplier: 'Папа наполи',
      candidates: [],
    },
    {
      card_id: 24,
      name: 'Оснвова для пиццы круглая , неаполитанская.',
      link_status: 'orphan',
      supplier: 'Папа наполи',
      candidates: [],
    },
  ],
}

const INGREDIENTY = [
  {
    id: 34,
    legacy_id: '34',
    name: 'Булочка для датского хот дога',
    category: 'Выпечка',
    unit: 'шт',
    status: 'активный',
    price_per_kg: '18.50',
    weight_per_piece_g: '60.000',
    has_card: true,
  },
  {
    id: 121,
    legacy_id: '121',
    name: 'Булочка для датского хот дога',
    category: 'Выпечка',
    unit: 'кг',
    status: 'активный',
    price_per_kg: '0.00',
    weight_per_piece_g: null,
    has_card: true,
  },
]

const server = setupServer(
  http.get('/api/reconciliation', () => HttpResponse.json(SVERKA)),
  http.get('/api/ingredients', () => HttpResponse.json(INGREDIENTY)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Reconciliation />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('сводка называет три числа', async () => {
  narisovat()
  expect(await screen.findByText('101')).toBeInTheDocument()
  expect(screen.getByText('85')).toBeInTheDocument()
  expect(screen.getByText('16')).toBeInTheDocument()
})

test('три группы стоят врозь: они требуют разных действий', async () => {
  narisovat()
  await screen.findByText('101')
  expect(screen.getByRole('heading', { name: /несколько совпадений/i })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /похожее/i })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /пары нет/i })).toBeInTheDocument()
})

test('предвыбранного варианта нет ни одного', async () => {
  // Один из кандидатов — «огурцы маринованные НЕ резаные» против
  // «Огурцы маринованные резанные», похожесть 95 %, смысл противоположный.
  // Подсвеченное как очевидное человек примет не глядя.
  narisovat()
  await screen.findByText('101')
  for (const perekl of screen.queryAllByRole('radio')) {
    expect(perekl).not.toBeChecked()
  }
})

test('кнопки «связать всё автоматически» не существует', async () => {
  narisovat()
  await screen.findByText('101')
  expect(screen.queryByRole('button', { name: /автоматич/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /связать всё/i })).not.toBeInTheDocument()
})

test('тёзки различаются ценой и единицей, а не именем', async () => {
  // Ручка отдаёт только id и имя. Цену подтягиваем из /api/ingredients:
  // выбирать шеф будет по ней.
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(within(gruppa).getByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(within(gruppa).getByText('0,00 ₽/кг')).toBeInTheDocument()
})

test('грязные данные показываются как есть', async () => {
  // Подчистить за шефа значило бы скрыть, что запись требует внимания.
  narisovat()
  expect(
    await screen.findByText('Оснвова для пиццы круглая , неаполитанская.'),
  ).toBeInTheDocument()
  expect(screen.getByText('Хз')).toBeInTheDocument()
})

test('действия обозначены, но объявлены недоступными', async () => {
  narisovat()
  await screen.findByText('101')
  const knopka = screen.getAllByRole('button', { name: /связать/i })[0]!
  expect(knopka).toBeDisabled()
  expect(screen.getAllByText(/появится в следующей фазе/i).length).toBeGreaterThan(0)
})

test('у каждой группы свои действия, как в разделе 8 спеки', async () => {
  // «Похожее» подтверждают или отвергают, «пары нет» — заводят или
  // откладывают. Одинаковые кнопки во всех группах стёрли бы разницу,
  // ради которой группы и стоят врозь.
  narisovat()
  const pohozhee = await screen.findByTestId('kartochka-25')
  expect(within(pohozhee).getByRole('button', { name: 'Связать' })).toBeDisabled()
  expect(within(pohozhee).getByRole('button', { name: 'Отвергнуть' })).toBeDisabled()

  const sirota = screen.getByTestId('kartochka-24')
  expect(within(sirota).getByRole('button', { name: 'Завести в справочник' })).toBeDisabled()
  expect(within(sirota).getByRole('button', { name: 'Отложить' })).toBeDisabled()
})

test('справочник не загрузился — экран говорит об этом, а не молчит', async () => {
  // Без цен тёзки неотличимы, а без сообщения экран выглядел бы целым.
  server.use(http.get('/api/ingredients', () => HttpResponse.json({}, { status: 500 })))
  narisovat()
  expect(await screen.findByText(/цены из справочника не загрузились/i)).toBeInTheDocument()
  // Сама сверка при этом показана: её ручка ответила.
  expect(screen.getByTestId('kartochka-7')).toBeInTheDocument()
})

test('позиции нет в справочнике — прочерк, а не пустота', async () => {
  server.use(http.get('/api/ingredients', () => HttpResponse.json(INGREDIENTY.slice(0, 1))))
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(await within(gruppa).findByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(within(gruppa).getByLabelText('значения нет')).toBeInTheDocument()
})
