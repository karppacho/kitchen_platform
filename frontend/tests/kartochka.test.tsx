import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { DishDetailPage } from '../src/pages/DishDetail'

// Ответ настоящий, из раздела 4 ТЗ.
const B001 = {
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
  protein_g: '4.1',
  fat_g: '14.1',
  carbs_g: '3.6',
  kcal: '159',
  kbju_coverage: '0.778',
  components: [
    {
      name: 'Салат айсберг',
      short_name: 'Салат айсберг пф',
      row_type: 'main',
      unit: 'кг',
      net_weight_g: '16.000',
      gross_weight_g: '22.19',
      price_per_unit: '213.38',
      cost_rub: '4.74',
      share_percent: '5.6',
    },
    {
      name: 'Контейнер бумажный без крышки 207х127х55 крафт/черный',
      short_name: '',
      row_type: 'packaging',
      unit: 'шт',
      net_weight_g: '1.000',
      gross_weight_g: null,
      price_per_unit: '7.99',
      cost_rub: '7.99',
      share_percent: '9.4',
    },
  ],
  warning_texts: ['КБЖУ нет у 1 ингр. (Салат айсберг) — нутриенты приблизительны'],
}

const server = setupServer(http.get('/api/dishes/B001', () => HttpResponse.json(B001)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const vid = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/dishes/B001']}>
        <Routes>
          <Route path="/dishes/:legacyId" element={<DishDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
}

test('нетто и брутто — две разные колонки', async () => {
  // 16 г айсберга в блюде требуют 22,19 г со склада из-за потерь при
  // нарезке. Схлопывать их в одну колонку нельзя.
  narisovat()
  await screen.findByText('Салат айсберг')

  expect(screen.getByText('16 г')).toBeInTheDocument()
  expect(screen.getByText('22,19 г')).toBeInTheDocument()
})

test('замечания показаны текстом целиком, а не числом', async () => {
  // Именно они объясняют, почему число такое.
  narisovat()
  expect(
    await screen.findByText(/КБЖУ нет у 1 ингр\. \(Салат айсберг\)/),
  ).toBeInTheDocument()
})

test('покрытие КБЖУ показано долей веса', async () => {
  // У B001 это 0.778: у айсберга КБЖУ не заполнено, а весит он почти
  // четверть блюда. Число выше порога 0.5, поэтому предупреждения нет.
  narisovat()
  await screen.findByText('Салат айсберг')
  expect(screen.getByText(/77,8\s*%/)).toBeInTheDocument()
  expect(screen.queryByText(/доверять нельзя/)).not.toBeInTheDocument()
})

test('покрытие ниже половины помечается прямо', async () => {
  // Ниже 0.5 цифрам КБЖУ доверять нельзя, и это надо сказать словами, а
  // не оставить читателю самому делить в уме.
  server.use(
    http.get('/api/dishes/B001', () => HttpResponse.json({ ...B001, kbju_coverage: '0.312' })),
  )
  narisovat()

  expect(await screen.findByText(/доверять нельзя/)).toBeInTheDocument()
})

test('упаковка стоит отдельной группой и брутто у неё нет', async () => {
  narisovat()
  const upakovka = await screen.findByText(/Контейнер бумажный/)
  expect(screen.getByRole('heading', { name: /упаковка/i })).toBeInTheDocument()
  expect(upakovka.closest('tr')!.querySelectorAll('.num--pusto').length).toBeGreaterThan(0)
})

test('пустое короткое имя не оставляет пустоту', async () => {
  narisovat()
  const upakovka = await screen.findByText(/Контейнер бумажный/)
  expect(upakovka).toBeInTheDocument()
})

// Ниже — тесты сверх дословного текста брифа: они покрывают требования,
// которые в примере кода брифа не были показаны, но прямо сформулированы
// в задании (правила показа для задачи 12).

test('себестоимость выше цены меню — красным, без слова «убыток»', async () => {
  // Та же логика, что на экране списка блюд: почти всегда это перепутанная
  // единица измерения, а не настоящий убыток.
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ ...B001, price_menu: '50.00', uc_rub: '84.66' }),
    ),
  )
  narisovat()

  const podskazka = await screen.findByText(/проверьте единицы измерения/i)
  expect(podskazka).toBeInTheDocument()
  expect(screen.queryByText(/убыток/i)).not.toBeInTheDocument()
})

test('блюда с таким legacy_id нет — понятное сообщение, не «не удалось получить данные»', async () => {
  // 404 — не то же самое, что «не удалось получить данные»: это не сбой
  // связи или сервера, а «такой карточки не существует».
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ detail: 'Блюдо не найдено' }, { status: 404 }),
    ),
  )
  narisovat()

  expect(await screen.findByText('Такого блюда нет')).toBeInTheDocument()
  expect(screen.queryByText('Не удалось получить данные')).not.toBeInTheDocument()
  // Повторять запрос за несуществующей карточкой бессмысленно: тот же
  // legacy_id вернёт тот же 404.
  expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
})

test('блюдо удалено из таблицы — своё сообщение, а не «такого нет»', async () => {
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ detail: 'блюдо удалено из таблицы' }, { status: 410 }),
    ),
  )
  narisovat()

  expect(await screen.findByRole('heading', { name: 'Блюдо удалено из таблицы' })).toBeInTheDocument()
  expect(screen.queryByText('Такого блюда нет')).not.toBeInTheDocument()
})

test('блюдо удалили, пока карточка открыта, — после перезапроса сообщение, а не прежние данные', async () => {
  // Главный путь к 410: шеф убрал строку из листа, синхронизация сменила
  // changed_at, строка свежести перезапросила карточку. Прежние данные в кэше
  // есть, но это не сбой связи — «не удалось обновить, на экране прежние
  // данные» здесь было бы неправдой: блюда на сайте больше нет.
  const { queries } = narisovat()
  await screen.findByText('Салат айсберг')
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ detail: 'блюдо удалено из таблицы' }, { status: 410 }),
    ),
  )
  await act(() => queries.refetchQueries())

  expect(await screen.findByRole('heading', { name: 'Блюдо удалено из таблицы' })).toBeInTheDocument()
  expect(screen.queryByText('Салат айсберг')).not.toBeInTheDocument()
})

test('состав и упаковка — одна таблица, колонки не разъезжаются', async () => {
  // Две таблицы с автоматической шириной считали колонки каждая по своим
  // данным: длинное имя контейнера растягивало первую колонку упаковки, и
  // её цифры уезжали вправо от цифр состава. В макете упаковка — группа
  // строк в той же таблице.
  const { container } = narisovat()
  const upakovka = await screen.findByText(/Контейнер бумажный/)
  const tablitsy = container.querySelectorAll('table')
  expect(tablitsy).toHaveLength(1)
  expect(upakovka.closest('table')).toBe(tablitsy[0])
  // Заголовок группы — на всю ширину таблицы, а не лишняя колонка.
  const zagolovok = screen.getByRole('heading', { name: /упаковка/i }).closest('th')!
  expect(zagolovok.colSpan).toBe(container.querySelectorAll('thead th').length)
})

test('у упаковки количество в штуках, а не в граммах', async () => {
  // Упаковка считается «цена за штуку × количество» (domain/costs.py,
  // add_packaging): в net_weight_g у неё штуки. «1 г» у контейнера —
  // неправда, которую шеф видит на каждой карточке.
  narisovat()
  const stroka = (await screen.findByText(/Контейнер бумажный/)).closest('tr')!
  expect(within(stroka).getByText('1 шт')).toBeInTheDocument()
  expect(within(stroka).queryByText('1 г')).not.toBeInTheDocument()
})

test('без упаковки группы нет', async () => {
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({
        ...B001,
        components: B001.components.filter((k) => k.row_type === 'main'),
      }),
    ),
  )
  narisovat()
  await screen.findByText('Салат айсберг')
  expect(screen.queryByRole('heading', { name: /упаковка/i })).not.toBeInTheDocument()
})

test('без замечаний нет и раздела замечаний', async () => {
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ ...B001, warnings: 0, warning_texts: [] }),
    ),
  )
  narisovat()
  await screen.findByText('Салат айсберг')
  expect(screen.queryByRole('heading', { name: /замечания/i })).not.toBeInTheDocument()
})

test('сбой фонового обновления не стирает показанное', async () => {
  // Вернулся во вкладку на кухонном Wi-Fi — обновление упало. Прежние данные
  // верны, пока не пришли новые; стирать их — отнять экран из-за связи.
  const { queries } = narisovat()
  await screen.findByText('Салат айсберг')
  server.use(http.get('/api/dishes/B001', () => HttpResponse.error()))
  await act(() => queries.refetchQueries())
  expect(await screen.findByText(/не удалось обновить/i)).toBeInTheDocument()
  expect(screen.getByText('Салат айсберг')).toBeInTheDocument()
})

test('отказ по правам на фоновом обновлении убирает данные', async () => {
  // В отличие от сбоя связи: 403 значит, что доступ отозван, и прежние
  // данные больше не показываем.
  const { queries } = narisovat()
  await screen.findByText('Салат айсберг')
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ detail: 'нужна роль: chef' }, { status: 403 }),
    ),
  )
  await act(() => queries.refetchQueries())
  expect(await screen.findByText('Доступа нет')).toBeInTheDocument()
  expect(screen.queryByText('Салат айсберг')).not.toBeInTheDocument()
})
