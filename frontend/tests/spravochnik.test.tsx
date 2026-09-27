import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'vitest'

import type { Ingredient } from '../src/api/types'
import { Ingredients } from '../src/pages/Ingredients'
import { setViewport } from './setup'

// Ответ настоящий: тот самый случай с двумя «Сахарами», из-за которого
// сверка не может решить сама.
// id и legacy_id намеренно различаются: тест «показывает legacy_id, а не
// внутренний» иначе не мог бы отличить одно от другого — при совпадающих
// значениях он прошёл бы и при ошибочном показе внутреннего id.
const SAHAR: Ingredient[] = [
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

// Для поиска, сортировки и фильтров: «ё» в названии; id, которые текстом
// сортируются иначе, чем числами («1000» < «12»); цены нет; штучная
// единица; нет карточки — у каждой позиции своё. Порядок ответа — ни по id,
// ни по названию: иначе выключенная сортировка была бы неотличима от
// включённой.
const RAZNYE: Ingredient[] = [
  {
    id: 1,
    legacy_id: '123',
    name: 'Свёкла',
    category: 'Овощи',
    unit: 'кг',
    status: 'активный',
    price_per_kg: '45.00',
    weight_per_piece_g: null,
    has_card: true,
  },
  {
    id: 2,
    legacy_id: '1000',
    name: 'Молоко',
    category: 'Молочное',
    unit: 'л',
    status: 'активный',
    price_per_kg: null,
    weight_per_piece_g: null,
    has_card: true,
  },
  {
    id: 3,
    legacy_id: '12',
    name: 'Яйцо',
    category: 'Яйца',
    unit: 'шт',
    status: 'архивный',
    price_per_kg: '12.50',
    weight_per_piece_g: '55.000',
    has_card: false,
  },
]

let otvet: Ingredient[] = SAHAR
// Адреса всех запросов справочника — чтобы видеть и их число, и параметры.
let zaprosy: URL[] = []

const server = setupServer(
  http.get('/api/ingredients', ({ request }) => {
    zaprosy.push(new URL(request.url))
    return HttpResponse.json(otvet)
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(() => {
  otvet = SAHAR
  zaprosy = []
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

let adres = ''

function Zond() {
  adres = useLocation().search
  return null
}

function narisovat(nachalo = '/') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const vid = render(
    <QueryClientProvider client={queries}>
      {/* Флаги — явно выключенные, как в приложении: поведение то же, а
          предупреждение о будущих флагах не печатается. */}
      <MemoryRouter initialEntries={[nachalo]} future={{ v7_startTransition: false, v7_relativeSplatPath: false }}>
        <Ingredients />
        <Zond />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
}

const podozhdat = (ms: number) => act(() => new Promise((gotovo) => setTimeout(gotovo, ms)))
const parametry = () => [...new URLSearchParams(adres)]
// Поле поиска появляется вместе с таблицей — когда справочник пришёл.
const poisk = () => screen.findByRole('searchbox', { name: 'Поиск по названию или id' })
const nabrat = async (tekst: string) => fireEvent.change(await poisk(), { target: { value: tekst } })
const zagolovok = (title: string) => screen.getByRole('columnheader', { name: title })
const knopkaSortirovki = (title: string) => within(zagolovok(title)).getByRole('button', { name: title })
const panel = (title: string) => screen.getByRole('dialog', { name: `Фильтр: ${title}` })
// id строк сверху вниз — первая ячейка каждой строки таблицы (на 1440 px).
const idStrok = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((tr) => within(tr).getAllByRole('cell')[0]!.textContent)
// Подписи галочек — «значение · счётчик», по порядку.
const podpisiGalochek = (gde: HTMLElement) =>
  within(gde)
    .getAllByRole('checkbox')
    .map((g) => g.closest('label')?.textContent)

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
  // Слово «архивный» бывает на странице не только в ячейке статуса — ещё
  // в галочке фильтра и в фишке, — поэтому берём именно строку архивного
  // ингредиента (legacy_id «123») и ищем статус внутри неё.
  narisovat()
  await screen.findAllByText('Сахар')

  const stroka = screen.getByText('123').closest('tr')
  expect(stroka).not.toBeNull()
  expect(within(stroka as HTMLElement).getByText('архивный')).toBeInTheDocument()
})

test.each(['архив', 'архивный'])('архивные оформлены и при статусе «%s»', async (status) => {
  // Статус — строка из таблицы шефа: бывает и «архив», и «архивный».
  // Справочник помечает оба, а не одно зашитое в код слово.
  otvet = [
    { ...SAHAR[0]!, name: 'Мука', status: 'активный' },
    { ...SAHAR[1]!, name: 'Крахмал', status },
  ]
  narisovat()
  expect(await screen.findByText('Крахмал')).toHaveClass('arhivnyy')
  expect(screen.getByText('Мука')).not.toHaveClass('arhivnyy')
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

test('на 1440 px «Ед.» — сразу после «Категории»', async () => {
  // Цена бывает за килограмм и за штуку — единицу видно и отобрать по ней
  // можно, не читая подпись под каждой ценой.
  narisovat()
  await poisk()
  expect(screen.getAllByRole('columnheader').map((th) => th.getAttribute('aria-label'))).toEqual([
    'id',
    'Наименование',
    'Категория',
    'Ед.',
    'Цена за единицу',
    'Вес 1 шт',
    'Статус',
    'Карточка',
  ])
  const stroka = screen.getByText('123').closest('tr') as HTMLElement
  expect(within(stroka).getAllByRole('cell')[3]).toHaveTextContent('шт')
})

test('справочник — одним запросом с limit=500, поиск и статус в него не идут', async () => {
  // Старая ссылка с поиском и статусом: и то и другое теперь отбирается в
  // браузере по полному списку.
  narisovat('/?search=сах&status=архивный')
  expect(await screen.findByText('Найдено: 1 из 2')).toBeInTheDocument()
  expect(zaprosy).toHaveLength(1)
  expect([...zaprosy[0]!.searchParams]).toEqual([['limit', '500']])
})

test('набор в поиске не шлёт запроса и не показывает «Загрузку…»', async () => {
  // Второй запрос, если бы он был, повис бы: «Загрузка…» осталась бы на
  // экране, а не проскочила между проверками.
  server.use(
    http.get('/api/ingredients', async ({ request }) => {
      zaprosy.push(new URL(request.url))
      if (zaprosy.length > 1) await delay('infinite')
      return HttpResponse.json(otvet)
    }),
  )
  const u = userEvent.setup()
  narisovat()
  await u.type(await poisk(), '123')
  // Поиск ушёл в адрес — а запроса за ним нет.
  await podozhdat(350)
  expect(parametry()).toEqual([['search', '123']])
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  expect(idStrok()).toEqual(['123'])
  expect(zaprosy).toHaveLength(1)
})

test('поиск — без учёта «ё» и регистра, и по id', async () => {
  otvet = RAZNYE
  narisovat()
  await nabrat('свекла')
  expect(idStrok()).toEqual(['123'])
  expect(screen.getByText('Найдено: 1 из 3')).toBeInTheDocument()
  await nabrat('1000')
  expect(idStrok()).toEqual(['1000'])
})

test('сортировка по id — как числа: 12, 123, 1000; третий щелчок — порядок ответа', async () => {
  // Текстом «1000» встало бы раньше «12».
  otvet = RAZNYE
  const u = userEvent.setup()
  narisovat()
  await poisk()
  expect(idStrok()).toEqual(['123', '1000', '12'])

  await u.click(knopkaSortirovki('id'))
  expect(idStrok()).toEqual(['12', '123', '1000'])
  expect(zagolovok('id')).toHaveAttribute('aria-sort', 'ascending')
  expect(parametry()).toEqual([['sort', 'id']])

  await u.click(knopkaSortirovki('id'))
  expect(idStrok()).toEqual(['1000', '123', '12'])
  expect(zagolovok('id')).toHaveAttribute('aria-sort', 'descending')
  expect(parametry()).toEqual([['sort', '-id']])

  await u.click(knopkaSortirovki('id'))
  expect(idStrok()).toEqual(['123', '1000', '12'])
  expect(zagolovok('id')).not.toHaveAttribute('aria-sort')
  expect(parametry()).toEqual([])
})

test('статус — галочкой в шапке: одна строка, адрес, «активный» остаётся в вариантах', async () => {
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(screen.getByRole('button', { name: 'Фильтр: Статус' }))
  await u.click(within(panel('Статус')).getByRole('checkbox', { name: 'архивный · 1' }))

  expect(idStrok()).toEqual(['123'])
  expect(parametry()).toEqual([['status', 'архивный']])
  // Собственный выбор список статусов не сужает: иначе «активный» пропал
  // бы, и вернуться к нему можно было бы только сбросом фильтра.
  expect(within(panel('Статус')).getByRole('checkbox', { name: 'активный · 1' })).not.toBeChecked()
  expect(within(panel('Статус')).getByRole('checkbox', { name: 'архивный · 1' })).toBeChecked()
})

test('старая ссылка ?status=архивный открывает отобранный список, варианты статуса — полные', async () => {
  // Ссылку, присланную до сортировки и фильтров, шеф открывает тем же
  // экраном — сразу только архивные, без повторного выбора.
  const u = userEvent.setup()
  narisovat('/?status=архивный')
  await poisk()
  expect(screen.getAllByText('Сахар')).toHaveLength(1)
  expect(screen.getByText('123')).toBeInTheDocument()
  expect(screen.queryByText('12')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Снять фильтр «Статус: архивный»' })).toBeInTheDocument()

  await u.click(screen.getByRole('button', { name: 'Фильтр: Статус, выбрано 1' }))
  expect(podpisiGalochek(panel('Статус'))).toEqual(['активный · 1', 'архивный · 1'])
  expect(within(panel('Статус')).getByRole('checkbox', { name: 'архивный · 1' })).toBeChecked()
  // Открытие ссылки адреса не меняет.
  expect(parametry()).toEqual([['status', 'архивный']])
})

test('варианты статуса — из ответа, а не из кода', async () => {
  // В настоящих данных статусы бывают и «активное» / «архив»: зашитый в
  // код список с ними бы не совпал.
  otvet = [
    { ...SAHAR[0]!, status: 'активное' },
    { ...SAHAR[1]!, status: 'архив' },
  ]
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(screen.getByRole('button', { name: 'Фильтр: Статус' }))
  expect(podpisiGalochek(panel('Статус'))).toEqual(['активное · 1', 'архив · 1'])
})

test.each([
  { kolonka: 'Ед.', galochka: 'шт · 1', id: ['12'], adres: [['unit', 'шт']], fishka: 'Ед.: шт' },
  { kolonka: 'Карточка', galochka: 'нет · 1', id: ['12'], adres: [['card', 'net']], fishka: 'Карточка: нет' },
  {
    kolonka: 'Цена за единицу',
    galochka: 'нет · 1',
    id: ['1000'],
    adres: [['price', 'net']],
    fishka: 'Цена за единицу: нет',
  },
])('фильтр $fishka', async ({ kolonka, galochka, id, adres: ozhidaemyy, fishka }) => {
  otvet = RAZNYE
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(screen.getByRole('button', { name: `Фильтр: ${kolonka}` }))
  await u.click(within(panel(kolonka)).getByRole('checkbox', { name: galochka }))
  expect(idStrok()).toEqual(id)
  expect(parametry()).toEqual(ozhidaemyy)
  expect(screen.getByRole('button', { name: `Снять фильтр «${fishka}»` })).toBeInTheDocument()
})

test('ответ упёрся в предел 500 строк — предупреждение, что справочник мог прийти не целиком', async () => {
  // Иначе поиск молча не нашёл бы позицию, не поместившуюся в ответ.
  otvet = Array.from({ length: 500 }, (_, i) => ({ ...SAHAR[0]!, id: i + 1, legacy_id: String(i + 1) }))
  narisovat()
  expect(await screen.findByText(/Показаны первые 500 позиций/)).toBeInTheDocument()
})

test('ответ меньше предела — предупреждения нет', async () => {
  narisovat()
  await poisk()
  expect(screen.queryByText(/Показаны первые/)).not.toBeInTheDocument()
})

test('перезапрос по строке свежести: второй запрос, вид прежний, «Загрузки…» нет', async () => {
  // Синхронизация перенесла правки из таблицы — строка свежести
  // перезапрашивает экраны по префиксу ['ingredients']. Шеф при этом не
  // должен терять ни сортировку, ни таблицу на время запроса.
  const { queries } = narisovat('/?sort=-id')
  await poisk()
  // Второй ответ держим, пока не посмотрим на экран во время перезапроса.
  let otpustit = () => {}
  server.use(
    http.get('/api/ingredients', async ({ request }) => {
      zaprosy.push(new URL(request.url))
      await new Promise<void>((gotovo) => {
        otpustit = gotovo
      })
      return HttpResponse.json(otvet)
    }),
  )
  act(() => {
    void queries.invalidateQueries({ queryKey: ['ingredients'] })
  })
  await waitFor(() => expect(zaprosy).toHaveLength(2))
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  expect(idStrok()).toEqual(['123', '12'])

  otpustit()
  await waitFor(() => expect(queries.isFetching()).toBe(0))
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  expect(adres).toBe('?sort=-id')
  expect(zagolovok('id')).toHaveAttribute('aria-sort', 'descending')
  expect(idStrok()).toEqual(['123', '12'])
})

test('сбой фонового обновления не стирает показанное', async () => {
  // Вернулся во вкладку на кухонном Wi-Fi — обновление упало. Прежние данные
  // верны, пока не пришли новые; стирать их — отнять экран из-за связи.
  const { queries } = narisovat()
  await screen.findAllByText('Сахар')
  server.use(http.get('/api/ingredients', () => HttpResponse.error()))
  await act(() => queries.refetchQueries())
  expect(await screen.findByText(/не удалось обновить/i)).toBeInTheDocument()
  expect(screen.getAllByText('Сахар')[0]).toBeInTheDocument()
})
