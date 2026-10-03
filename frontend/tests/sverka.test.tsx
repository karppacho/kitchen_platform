import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Ingredients } from '../src/pages/Ingredients'
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
      approved: true,
      candidates: [
        { ingredient_id: 34, legacy_id: '34', name: 'Булочка для датского хот дога', score: null },
        { ingredient_id: 121, legacy_id: '121', name: 'Булочка для датского хот дога', score: null },
      ],
      actions: ['confirm', 'to_reference'],
    },
    {
      card_id: 25,
      name: 'Корж для римской пиццы',
      link_status: 'candidate',
      supplier: 'Папа наполи',
      approved: true,
      candidates: [{ ingredient_id: 34, legacy_id: '34', name: 'Корж для пиццы', score: 0.85 }],
      actions: ['confirm', 'to_reference'],
    },
    {
      card_id: 24,
      name: 'Оснвова для пиццы круглая , неаполитанская.',
      link_status: 'orphan',
      supplier: 'Папа наполи',
      approved: true,
      candidates: [],
      actions: ['to_reference'],
    },
    {
      card_id: 26,
      name: 'Тесто слоёное',
      link_status: 'orphan',
      supplier: 'Метро',
      approved: false,
      candidates: [],
      actions: [],
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
  const vid = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Reconciliation />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
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

test('недоступное действие объявлено: «Это он» ждёт выбора, несогласованной — пояснение', async () => {
  // «Это он» без выбранного варианта подтвердил бы «не глядя» — кнопка ждёт
  // выбора. Карточке не «Да» в справочник нельзя — вместо кнопки сказано
  // почему, а не пустое место.
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  const knopka = within(gruppa).getByRole('button', { name: 'Это он' })
  expect(knopka).toBeDisabled()
  await userEvent.click(within(gruppa).getAllByRole('radio')[0]!)
  expect(knopka).toBeEnabled()

  const nesoglasovannaya = screen.getByTestId('kartochka-26')
  expect(within(nesoglasovannaya).queryByRole('button')).toBe(null)
  expect(within(nesoglasovannaya).getByText(/в справочник попадают только «Да»/)).toBeInTheDocument()
})

test('у каждой группы свои действия, как в решении 7 спеки', async () => {
  // Спорную пару подтверждают («Это он») или признают новой («Это новый»),
  // карточку без пары добавляют в справочник. Одинаковые кнопки во всех
  // группах стёрли бы разницу, ради которой группы и стоят врозь.
  narisovat()
  const pohozhee = await screen.findByTestId('kartochka-25')
  expect(
    within(pohozhee).getByRole('button', { name: 'Это он: «Корж для пиццы»' }),
  ).toBeInTheDocument()
  expect(within(pohozhee).getByRole('button', { name: 'Это новый' })).toBeEnabled()
  expect(within(pohozhee).queryByRole('button', { name: 'Добавить в справочник' })).toBe(null)

  const sirota = screen.getByTestId('kartochka-24')
  expect(within(sirota).getByRole('button', { name: 'Добавить в справочник' })).toBeEnabled()
  expect(within(sirota).queryByRole('button', { name: /^Это он/ })).toBe(null)
})

test('справочник не загрузился — экран говорит об этом, а не молчит', async () => {
  // Без цен тёзки неотличимы, а без сообщения экран выглядел бы целым.
  server.use(http.get('/api/ingredients', () => HttpResponse.json({}, { status: 500 })))
  narisovat()
  expect(await screen.findByText(/цены из справочника не загрузились/i)).toBeInTheDocument()
  // Сама сверка при этом показана: её ручка ответила.
  expect(screen.getByTestId('kartochka-7')).toBeInTheDocument()
})

test('справочник для цен сверка берёт с limit=500', async () => {
  // Тот же запрос, что у экрана справочника: по умолчанию ручка отдаёт 200
  // строк, и позиции за ними остались бы без цены.
  const zaprosy: URL[] = []
  server.use(
    http.get('/api/ingredients', ({ request }) => {
      zaprosy.push(new URL(request.url))
      return HttpResponse.json(INGREDIENTY)
    }),
  )
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(await within(gruppa).findByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(zaprosy.map((url) => [...url.searchParams])).toEqual([[['limit', '500']]])
})

test('сверка и экран справочника делят один запрос', async () => {
  // Кэш общий: открыть справочник после сверки — без второй загрузки.
  const zaprosy: URL[] = []
  server.use(
    http.get('/api/ingredients', ({ request }) => {
      zaprosy.push(new URL(request.url))
      return HttpResponse.json(INGREDIENTY)
    }),
  )
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Reconciliation />
        <Ingredients />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(await within(gruppa).findByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(await screen.findByText('Найдено: 2 из 2')).toBeInTheDocument()
  expect(zaprosy).toHaveLength(1)
})

test('позиции нет в справочнике — прочерк, а не пустота', async () => {
  server.use(http.get('/api/ingredients', () => HttpResponse.json(INGREDIENTY.slice(0, 1))))
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(await within(gruppa).findByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(within(gruppa).getByLabelText('значения нет')).toBeInTheDocument()
})

test('сбой фонового обновления не стирает показанное', async () => {
  // Вернулся во вкладку на кухонном Wi-Fi — обновление упало. Прежние данные
  // верны, пока не пришли новые; стирать их — отнять экран из-за связи.
  const { queries } = narisovat()
  await screen.findByText('101')
  server.use(http.get('/api/reconciliation', () => HttpResponse.error()))
  await act(() => queries.refetchQueries())
  expect(await screen.findByText(/не удалось обновить/i)).toBeInTheDocument()
  expect(screen.getByText('101')).toBeInTheDocument()
})
