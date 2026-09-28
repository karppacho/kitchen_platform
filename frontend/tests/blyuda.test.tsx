import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'vitest'

import type { Dish, DishDetail } from '../src/api/types'
import { DishDetailPage } from '../src/pages/DishDetail'
import { Dishes } from '../src/pages/Dishes'
import { setViewport } from './setup'

// Ответы настоящие: B001 с ценой, B003 из тех четырнадцати, у кого её нет,
// и выдуманная строка, где себестоимость выше цены.
const BLYUDA: Dish[] = [
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

// Карточка блюда — для сквозных тестов «открыть блюдо и вернуться к списку».
function kartochka(b: Dish): DishDetail {
  return {
    ...b,
    protein_g: '4.1',
    fat_g: '14.1',
    carbs_g: '3.6',
    kcal: '159',
    kbju_coverage: '0.778',
    components: [],
    warning_texts: [],
  }
}

let otvet: Dish[] = BLYUDA
// Адреса всех запросов списка — чтобы видеть и их число, и параметры.
let zaprosy: URL[] = []

const server = setupServer(
  http.get('/api/dishes', ({ request }) => {
    zaprosy.push(new URL(request.url))
    return HttpResponse.json(otvet)
  }),
  http.get('/api/dishes/:legacyId', ({ params }) => {
    const b = otvet.find((r) => r.legacy_id === params.legacyId)
    return b ? HttpResponse.json(kartochka(b)) : HttpResponse.json({ detail: 'Блюдо не найдено' }, { status: 404 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
beforeEach(() => {
  otvet = BLYUDA
  zaprosy = []
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

let adres = ''
let put = ''

function Zond() {
  const mesto = useLocation()
  adres = mesto.search
  put = mesto.pathname
  return null
}

function narisovat(nachalo = '/dishes') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const vid = render(
    <QueryClientProvider client={queries}>
      {/* Флаги — явно выключенные, как в приложении: поведение то же, а
          предупреждение о будущих флагах не печатается. */}
      <MemoryRouter initialEntries={[nachalo]} future={{ v7_startTransition: false, v7_relativeSplatPath: false }}>
        <Routes>
          <Route path="/dishes" element={<Dishes />} />
          <Route path="/dishes/:legacyId" element={<DishDetailPage />} />
        </Routes>
        <Zond />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
}

const podozhdat = (ms: number) => act(() => new Promise((gotovo) => setTimeout(gotovo, ms)))
const parametry = () => [...new URLSearchParams(adres)]
// Поле поиска появляется вместе с таблицей — когда список пришёл.
const poisk = () => screen.findByRole('searchbox', { name: 'Поиск по названию или id' })
const zagolovok = (title: string) => screen.getByRole('columnheader', { name: title })
const knopkaSortirovki = (title: string) => within(zagolovok(title)).getByRole('button', { name: title })
const panel = (title: string) => screen.getByRole('dialog', { name: `Фильтр: ${title}` })
// id строк сверху вниз — первая ячейка каждой строки таблицы (на 1440 px).
const idStrok = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((tr) => within(tr).getAllByRole('cell')[0]!.textContent)
// Названия блюд сверху вниз — на узком экране, где id не видно.
const NAZVANIYA = new RegExp(`^(${BLYUDA.map((b) => b.name).join('|')})$`)
const nazvaniya = () => screen.queryAllByText(NAZVANIYA).map((el) => el.textContent)

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

test('на 1440 px «Статус» — сразу после «Категории», «Замечания» — последней', async () => {
  // Статус виден и в списке, а не только в карточке: по нему отбирают.
  // «Замечания» остаются последней — на этом держится тест «ноль замечаний».
  narisovat()
  await poisk()
  expect(screen.getAllByRole('columnheader').map((th) => th.getAttribute('aria-label'))).toEqual([
    'id',
    'Название',
    'Категория',
    'Статус',
    'Цена меню',
    'UC ₽',
    'UC %',
    'Маржа %',
    'Выход',
    'Замечания',
  ])
  const stroka = screen.getByText('B001').closest('tr') as HTMLElement
  expect(within(stroka).getAllByRole('cell')[3]).toHaveTextContent('активное')
})

test('маржа — как числа со знаком, прочерк в конце в обе стороны', async () => {
  // Текстом «-399.3» встало бы после «77.1», а блюдо без цены меню (маржи
  // нет) — в начало одной из сторон.
  const u = userEvent.setup()
  narisovat()
  await poisk()

  await u.click(knopkaSortirovki('Маржа %'))
  expect(idStrok()).toEqual(['B099', 'B001', 'B003'])
  expect(zagolovok('Маржа %')).toHaveAttribute('aria-sort', 'ascending')
  expect(parametry()).toEqual([['sort', 'margin']])

  await u.click(knopkaSortirovki('Маржа %'))
  expect(idStrok()).toEqual(['B001', 'B099', 'B003'])
  expect(zagolovok('Маржа %')).toHaveAttribute('aria-sort', 'descending')
  expect(parametry()).toEqual([['sort', '-margin']])
})

test('две отрицательные маржи — по величине, а не как текст', async () => {
  // Текстом «-5.0» встало бы раньше «-399.3»: после минуса сравнились бы
  // 5 и 399. Одна отрицательная (тест выше) этого не видит — минус и так
  // стоит раньше цифр.
  otvet = [...BLYUDA, { ...BLYUDA[2]!, legacy_id: 'B050', name: 'Салат с тунцом', margin_percent: '-5.0' }]
  const u = userEvent.setup()
  narisovat()
  await poisk()

  await u.click(knopkaSortirovki('Маржа %'))
  expect(idStrok()).toEqual(['B099', 'B050', 'B001', 'B003'])
  await u.click(knopkaSortirovki('Маржа %'))
  expect(idStrok()).toEqual(['B001', 'B050', 'B099', 'B003'])
})

test('на 360 px «Маржа %: сначала худшая» — тот же порядок и sort=margin', async () => {
  // Шеф ищет, где маржа хуже всего, — подпись говорит его словами, а не
  // «по возрастанию».
  setViewport(360)
  const u = userEvent.setup()
  narisovat()
  const sortirovka = await screen.findByRole('combobox', { name: 'Сортировка' })

  await u.selectOptions(sortirovka, 'Маржа %: сначала худшая')
  expect(nazvaniya()).toEqual(['Салат овощной', 'Круасан с мортаделой', 'Кетчуп'])
  expect(parametry()).toEqual([['sort', 'margin']])

  await u.selectOptions(sortirovka, 'Маржа %: сначала лучшая')
  expect(nazvaniya()).toEqual(['Круасан с мортаделой', 'Салат овощной', 'Кетчуп'])
  expect(parametry()).toEqual([['sort', '-margin']])
})

test.each([
  {
    kolonka: 'UC ₽',
    galochka: 'выше цены меню · 1',
    stroki: BLYUDA,
    id: ['B099'],
    adres: [['uc', 'vyshe']],
    fishka: 'UC ₽: выше цены меню',
  },
  {
    kolonka: 'Замечания',
    galochka: 'нет · 1',
    stroki: [BLYUDA[0]!, { ...BLYUDA[1]!, warnings: 0 }, BLYUDA[2]!],
    id: ['B003'],
    adres: [['warnings', 'net']],
    fishka: 'Замечания: нет',
  },
])('фильтр $fishka', async ({ kolonka, galochka, stroki, id, adres: ozhidaemyy, fishka }) => {
  otvet = stroki
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(screen.getByRole('button', { name: `Фильтр: ${kolonka}` }))
  await u.click(within(panel(kolonka)).getByRole('checkbox', { name: galochka }))
  expect(idStrok()).toEqual(id)
  expect(parametry()).toEqual(ozhidaemyy)
  expect(screen.getByRole('button', { name: `Снять фильтр «${fishka}»` })).toBeInTheDocument()
})

test('«Цена меню: нет» — только блюдо без цены; цена «0.00» — «есть»', async () => {
  // Прочерк и ноль значат разное: ноль — цена есть, просто нулевая.
  otvet = [{ ...BLYUDA[0]!, price_menu: '0.00' }, BLYUDA[1]!, BLYUDA[2]!]
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(screen.getByRole('button', { name: 'Фильтр: Цена меню' }))
  expect(within(panel('Цена меню')).getByRole('checkbox', { name: 'есть · 2' })).toBeInTheDocument()

  await u.click(within(panel('Цена меню')).getByRole('checkbox', { name: 'нет · 1' }))
  expect(idStrok()).toEqual(['B003'])
  expect(parametry()).toEqual([['price', 'net']])
  expect(screen.getByRole('button', { name: 'Снять фильтр «Цена меню: нет»' })).toBeInTheDocument()
})

test('старая ссылка ?search=…&status=активное: тот же отбор, одним запросом без параметров', async () => {
  // Ссылку, присланную до сортировки и фильтров, шеф открывает тем же
  // экраном. Поиск и статус теперь отбираются в браузере по полному списку.
  // Поиск «B0» находит все три блюда (по id) — отсеивает только статус.
  otvet = [BLYUDA[0]!, { ...BLYUDA[1]!, status: 'архив' }, BLYUDA[2]!]
  const u = userEvent.setup()
  narisovat('/dishes?search=B0&status=активное')
  await poisk()
  expect(idStrok()).toEqual(['B001', 'B099'])
  expect(screen.getByText('Найдено: 2 из 3')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Снять фильтр «Статус: активное»' })).toBeInTheDocument()
  expect(zaprosy).toHaveLength(1)
  expect([...zaprosy[0]!.searchParams]).toEqual([])

  // Собственный выбор список статусов не сужает: «архив» остаётся в
  // вариантах. Открытие ссылки адреса не меняет.
  await u.click(screen.getByRole('button', { name: 'Фильтр: Статус, выбрано 1' }))
  expect(within(panel('Статус')).getByRole('checkbox', { name: 'архив · 1' })).not.toBeChecked()
  expect(within(panel('Статус')).getByRole('checkbox', { name: 'активное · 2' })).toBeChecked()
  expect(parametry()).toEqual([
    ['search', 'B0'],
    ['status', 'активное'],
  ])
})

test('фильтр, выбранный в паузу поиска, не пропадает', async () => {
  // Поиск уходит в адрес через 300 мс после последней буквы. Галочка,
  // поставленная за это время, — тоже запись в адрес, и отложенная запись
  // поиска не должна её стереть. Всё синхронным fireEvent: между набором и
  // галочкой не успеет сработать ни один таймер.
  narisovat()
  fireEvent.change(await poisk(), { target: { value: 'кр' } })
  fireEvent.click(screen.getByRole('button', { name: 'Фильтр: Статус' }))
  fireEvent.click(within(panel('Статус')).getByRole('checkbox', { name: 'активное · 1' }))
  // Поиск ещё ждёт паузы — в адресе пока только галочка.
  expect(parametry()).toEqual([['status', 'активное']])

  await podozhdat(400)
  expect(parametry()).toEqual([
    ['search', 'кр'],
    ['status', 'активное'],
  ])
  expect(idStrok()).toEqual(['B001'])
})

test('перезапрос по строке свежести: вид прежний, «Загрузки…» нет', async () => {
  // Синхронизация перенесла правки из таблицы — строка свежести
  // перезапрашивает экраны по префиксу ['dishes']. Шеф при этом не должен
  // терять ни сортировку, ни таблицу на время запроса.
  const { queries } = narisovat('/dishes?sort=-margin')
  await poisk()
  // Второй ответ держим, пока не посмотрим на экран во время перезапроса.
  let otpustit = () => {}
  server.use(
    http.get('/api/dishes', async ({ request }) => {
      zaprosy.push(new URL(request.url))
      await new Promise<void>((gotovo) => {
        otpustit = gotovo
      })
      return HttpResponse.json(otvet)
    }),
  )
  act(() => {
    void queries.invalidateQueries({ queryKey: ['dishes'] })
  })
  await waitFor(() => expect(zaprosy).toHaveLength(2))
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  expect(idStrok()).toEqual(['B001', 'B099', 'B003'])

  otpustit()
  await waitFor(() => expect(queries.isFetching()).toBe(0))
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  expect(adres).toBe('?sort=-margin')
  expect(zagolovok('Маржа %')).toHaveAttribute('aria-sort', 'descending')
  expect(idStrok()).toEqual(['B001', 'B099', 'B003'])
})

test('отсортировал по убыванию, открыл блюдо — «← Блюда» возвращает к тому же виду', async () => {
  // Шеф идёт по списку сверху вниз и открывает блюда по одному: список,
  // вернувшийся без сортировки, заставлял бы сортировать заново каждый раз.
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(knopkaSortirovki('Маржа %'))
  await u.click(knopkaSortirovki('Маржа %'))

  await u.click(screen.getByText('Салат овощной'))
  const nazad = await screen.findByRole('link', { name: '← Блюда' })
  expect(put).toBe('/dishes/B099')

  await u.click(nazad)
  await poisk()
  expect(put).toBe('/dishes')
  expect(adres).toBe('?sort=-margin')
  expect(zagolovok('Маржа %')).toHaveAttribute('aria-sort', 'descending')
  expect(idStrok()).toEqual(['B001', 'B099', 'B003'])
})

test('поиск, набранный без паузы, «← Блюда» тоже возвращает', async () => {
  // Шеф набрал «сал» и сразу открыл блюдо: в адрес поиск ещё не ушёл, а
  // вернуться надо к нему же. Набор и щелчок — синхронным fireEvent: между
  // ними не успеет сработать ни один таймер.
  const u = userEvent.setup()
  narisovat()
  await poisk()
  await u.click(knopkaSortirovki('Маржа %'))
  await u.click(knopkaSortirovki('Маржа %'))

  fireEvent.change(await poisk(), { target: { value: 'сал' } })
  // Поиск ещё ждёт паузы — в адресе его нет.
  expect(adres).toBe('?sort=-margin')
  fireEvent.click(screen.getByText('Салат овощной'))
  expect(put).toBe('/dishes/B099')

  await u.click(await screen.findByRole('link', { name: '← Блюда' }))
  expect(await poisk()).toHaveValue('сал')
  expect(parametry()).toEqual([
    ['search', 'сал'],
    ['sort', '-margin'],
  ])
  expect(zagolovok('Маржа %')).toHaveAttribute('aria-sort', 'descending')
  expect(idStrok()).toEqual(['B099'])
})
