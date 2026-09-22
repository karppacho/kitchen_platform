import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/dishes/B001']}>
        <Routes>
          <Route path="/dishes/:legacyId" element={<DishDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
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

  const podskazka = await screen.findByTitle(/проверьте единицы измерения/i)
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
