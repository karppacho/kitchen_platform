import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'vitest'

import { KLYUCH_CHERNOVIKA } from '../src/api/kartochki'
import type { Draft, DraftPatch, NameCheck } from '../src/api/types'
import { App } from '../src/App'
import { RAZDELY } from '../src/shell/razdely'

const PERVYI = '0b6f3f5e-6a51-4f43-9a52-6f1d1c0e7a01'
const VTOROI = '7c1e2d3f-1b2a-4c5d-8e9f-0a1b2c3d4e5f'

/** Черновик, каким сервер отдаёт его сразу после «Начать». */
function novyi(id: string): Draft {
  return {
    id,
    status: 'active',
    step: 'supplier',
    supplier: '',
    category: '',
    name: '',
    label_name: '',
    manufacturer: '',
    composition: '',
    protein: null,
    fat: null,
    carbs: null,
    kcal: null,
    shelf_life_sealed: '',
    shelf_life_defrost: '',
    shelf_life_after: '',
    defrost_conditions: '',
    description: '',
    approval: null,
    photos: { label: false, package: false, before: false, after: false },
    recognition_status: null,
    recognition_error: null,
    warnings: [],
    missing: ['Поставщик', 'Категория', 'Название', 'Фото этикетки', 'Согласован ли продукт'],
    created_at: '2026-10-01T08:00:00Z',
    updated_at: '2026-10-01T08:00:00Z',
  }
}

const NICHEGO: NameCheck = {
  cards: { exact: [], similar: [] },
  hidden: { exact: [], similar: [] },
  reference: { exact: [], similar: [] },
}

// Что «сервер» знает и что к нему приходило — заново на каждый тест.
let roli: string[]
let chernovik: Draft | null
let pravki: DraftPatch[]
let zashchita: (string | null)[]
let udaleno: string[]
let sozdano: number

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'cook@example.com', display_name: 'Повар', roles: roli }),
  ),
  // Строка свежести оболочки ходит в /api/sync на каждом экране.
  http.get('/api/sync', () =>
    HttpResponse.json({ data_as_of: null, changed_at: null, stale: false, books: [] }),
  ),
  http.get('/api/dishes', () => HttpResponse.json([])),
  http.get('/api/ingredients', () => HttpResponse.json([])),
  http.get('/api/reconciliation', () =>
    HttpResponse.json({ total: 0, linked: 0, needs_human: 0, rows: [] }),
  ),
  http.get('/api/cards/drafts/current', () => HttpResponse.json(chernovik)),
  http.post('/api/cards/drafts', () => {
    if (chernovik) {
      return HttpResponse.json(
        { detail: 'У вас уже есть незаконченная карточка — продолжите её или начните заново' },
        { status: 409 },
      )
    }
    // Прежние черновики в тестах — PERVYI; заведённый здесь — всегда другой.
    sozdano += 1
    chernovik = novyi(VTOROI)
    return HttpResponse.json(chernovik, { status: 201 })
  }),
  http.patch('/api/cards/drafts/:id', async ({ request, params }) => {
    if (!chernovik || chernovik.id !== params.id) {
      return HttpResponse.json({ detail: 'Черновик не найден — обновите страницу' }, { status: 404 })
    }
    const pravka = (await request.json()) as DraftPatch
    pravki.push(pravka)
    zashchita.push(request.headers.get('X-Kitchen-Csrf'))
    chernovik = { ...chernovik, ...pravka } as Draft
    return HttpResponse.json(chernovik)
  }),
  http.delete('/api/cards/drafts/:id', ({ request, params }) => {
    zashchita.push(request.headers.get('X-Kitchen-Csrf'))
    udaleno.push(String(params.id))
    chernovik = null
    return new HttpResponse(null, { status: 204 })
  }),
  http.get('/api/cards/options', () =>
    HttpResponse.json({
      categories: ['Сыры', 'Мясо', 'Овощи'],
      suppliers: ['Метро', 'Овощебаза', 'Молочный двор'],
    }),
  ),
  http.get('/api/cards/name-check', ({ request }) => {
    const imya = new URL(request.url).searchParams.get('name')
    if (imya === 'Томаты') {
      return HttpResponse.json({
        ...NICHEGO,
        cards: { exact: [{ name: 'Томаты', supplier: 'Овощебаза' }], similar: [] },
      })
    }
    if (imya === 'Моцарела') {
      return HttpResponse.json({
        ...NICHEGO,
        reference: { exact: [], similar: ['Моцарелла фиор ди латте'] },
      })
    }
    return HttpResponse.json(NICHEGO)
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
beforeEach(() => {
  roli = ['cook']
  chernovik = null
  pravki = []
  zashchita = []
  udaleno = []
  sozdano = 0
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat(put = '/cards') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const vid = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[put]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
}

function punktyMenyu(): string[] {
  const menyu = screen.getByRole('navigation', { name: 'Разделы' })
  return within(menyu)
    .getAllByRole('link')
    .map((ssylka) => ssylka.textContent ?? '')
}

/** Черновик на нужном шаге — повар вернулся к нему и нажал «Продолжить». */
async function prodolzhit(chastichno: Partial<Draft>) {
  chernovik = { ...novyi(PERVYI), ...chastichno }
  const vid = narisovat()
  await userEvent.click(await screen.findByRole('button', { name: 'Продолжить' }))
  return vid
}

// ---------------------------------------------------------------------------
// Раздел повара в меню
// ---------------------------------------------------------------------------

test('повар видит в меню только «Новый ингредиент» и с главной попадает на /cards', async () => {
  narisovat('/')

  expect(await screen.findByRole('heading', { level: 1, name: 'Новый ингредиент' })).toBeInTheDocument()
  // Ни справочника, ни блюд, ни даже заглушек будущих разделов: сервер
  // отдаёт повару только карточки, остальное было бы отказом 403.
  expect(punktyMenyu()).toEqual(['Новый ингредиент'])
  expect(screen.getByRole('link', { name: 'Новый ингредиент' })).toHaveAttribute(
    'aria-current',
    'page',
  )
})

test('повару чужой адрес — не отказ 403, а его раздел', async () => {
  for (const put of ['/dishes', '/ingredients', '/pricing', '/net-takogo']) {
    const { unmount } = narisovat(put)
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Новый ингредиент' }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Доступа нет/)).not.toBeInTheDocument()
    unmount()
  }
})

test('шеф видит все разделы, «Новый ингредиент» — живой, главная — блюда', async () => {
  roli = ['chef']
  narisovat('/')

  expect(await screen.findByRole('heading', { level: 1, name: 'Блюда' })).toBeInTheDocument()
  expect(punktyMenyu()).toHaveLength(RAZDELY.length)
  // Живой раздел, а не заглушка «скоро».
  expect(screen.getByRole('link', { name: 'Новый ингредиент' })).not.toHaveAttribute(
    'aria-describedby',
  )
})

test('у нескольких ролей — объединение разделов; коммерсу «Новый ингредиент» не виден', async () => {
  roli = ['commerce', 'cook']
  const { unmount } = narisovat('/')
  await screen.findByRole('heading', { level: 1, name: 'Блюда' })
  expect(punktyMenyu()).toHaveLength(RAZDELY.length)
  unmount()

  roli = ['commerce']
  narisovat('/cards')
  // Коммерсу ручки карточек отвечают 403 — раздела у него нет, адрес ведёт
  // на главную.
  expect(await screen.findByRole('heading', { level: 1, name: 'Блюда' })).toBeInTheDocument()
  expect(punktyMenyu()).not.toContain('Новый ингредиент')
})

// ---------------------------------------------------------------------------
// Начало мастера
// ---------------------------------------------------------------------------

test('без черновика «Начать» заводит его и открывает шаг 1 из 9', async () => {
  narisovat()

  await userEvent.click(await screen.findByRole('button', { name: 'Начать' }))

  expect(await screen.findByText('Шаг 1 из 9')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Поставщик' })).toBeInTheDocument()
  expect(sozdano).toBe(1)
  expect(screen.queryByRole('button', { name: 'Продолжить' })).not.toBeInTheDocument()
})

test('есть незаконченная карточка — «Продолжить» открывает её шаг с её данными', async () => {
  chernovik = {
    ...novyi(PERVYI),
    supplier: 'Метро',
    category: 'Сыры',
    name: 'Моцарелла',
    step: 'name',
  }
  narisovat()

  // Повар видит, что именно он не закончил, — и выбирает сам.
  expect(await screen.findByText(/Моцарелла/)).toBeInTheDocument()
  expect(screen.getByText(/Шаг 3 из 9/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Начать заново' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Продолжить' }))

  expect(screen.getByRole('heading', { name: 'Название' })).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Название' })).toHaveValue('Моцарелла')
  expect(sozdano).toBe(0)
  expect(udaleno).toEqual([])
})

test('«Начать заново» сперва спрашивает, затем удаляет черновик и заводит новый', async () => {
  chernovik = { ...novyi(PERVYI), supplier: 'Метро', step: 'category' }
  narisovat()

  await userEvent.click(await screen.findByRole('button', { name: 'Начать заново' }))
  // Одно касание не стирает работу: черновик и его фото удаляются только
  // после подтверждения.
  expect(udaleno).toEqual([])
  expect(screen.getByText(/удалится/)).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Нет, оставить' }))
  expect(udaleno).toEqual([])
  expect(screen.getByRole('button', { name: 'Продолжить' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Начать заново' }))
  await userEvent.click(screen.getByRole('button', { name: 'Да, начать заново' }))

  expect(await screen.findByText('Шаг 1 из 9')).toBeInTheDocument()
  expect(udaleno).toEqual([PERVYI])
  expect(sozdano).toBe(1)
  expect(chernovik?.id).toBe(VTOROI)
  expect(screen.getByRole('textbox', { name: 'Поставщик' })).toHaveValue('')
  expect(zashchita).toEqual(['1'])
})

test('«Начать заново» во время отправки — текст сервера, нового черновика нет', async () => {
  server.use(
    http.delete('/api/cards/drafts/:id', () =>
      HttpResponse.json({ detail: 'Карточка отправляется — подождите' }, { status: 409 }),
    ),
  )
  chernovik = novyi(PERVYI)
  narisovat()

  await userEvent.click(await screen.findByRole('button', { name: 'Начать заново' }))
  await userEvent.click(screen.getByRole('button', { name: 'Да, начать заново' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Карточка отправляется — подождите')
  expect(sozdano).toBe(0)
})

// ---------------------------------------------------------------------------
// Шаги «Поставщик», «Категория», «Название»
// ---------------------------------------------------------------------------

test('пустой поставщик — «Далее» недоступна; вписанный или выбранный — доступна', async () => {
  await prodolzhit({ step: 'supplier' })

  const pole = screen.getByRole('textbox', { name: 'Поставщик' })
  const dalee = screen.getByRole('button', { name: 'Далее' })
  expect(dalee).toBeDisabled()

  // Одни пробелы — тоже пусто.
  await userEvent.type(pole, '   ')
  expect(dalee).toBeDisabled()

  await userEvent.clear(pole)
  await userEvent.type(pole, 'Новый поставщик')
  expect(dalee).toBeEnabled()

  // Поставщики из карточек — касанием, без набора.
  await userEvent.clear(pole)
  await userEvent.click(await screen.findByRole('button', { name: 'Овощебаза' }))
  expect(pole).toHaveValue('Овощебаза')

  await userEvent.click(dalee)
  expect(await screen.findByText('Шаг 2 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ supplier: 'Овощебаза', step: 'category' }])
})

test('«Другая…» принимает свою категорию', async () => {
  await prodolzhit({ supplier: 'Метро', step: 'category' })

  const dalee = screen.getByRole('button', { name: 'Далее' })
  expect(await screen.findByRole('radio', { name: 'Сыры' })).toBeInTheDocument()
  expect(dalee).toBeDisabled()

  await userEvent.click(screen.getByRole('radio', { name: 'Другая…' }))
  const svoya = screen.getByRole('textbox', { name: 'Своя категория' })
  // Выбрать «Другая…» и ничего не вписать — категории всё ещё нет.
  expect(dalee).toBeDisabled()

  await userEvent.type(svoya, 'Заморозка')
  expect(dalee).toBeEnabled()
  await userEvent.click(dalee)

  expect(await screen.findByText('Шаг 3 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ category: 'Заморозка', step: 'name' }])
})

test('дубль названия блокирует «Далее»', async () => {
  await prodolzhit({ supplier: 'Метро', category: 'Овощи', step: 'name' })

  await userEvent.type(screen.getByRole('textbox', { name: 'Название' }), 'Томаты')
  // Проверка ещё не ответила — «Далее» ждёт её: быстрое касание не должно
  // проскочить мимо дубля.
  const dalee = screen.getByRole('button', { name: 'Далее' })
  expect(dalee).toBeDisabled()

  // Такую строку писатель листа не запишет — повар узнаёт это сейчас, а не
  // на последнем шаге.
  expect(await screen.findByRole('alert')).toHaveTextContent(/«Томаты» уже есть в таблице/)
  expect(screen.getByRole('alert')).toHaveTextContent('Овощебаза')
  expect(dalee).toBeDisabled()
  expect(pravki).toEqual([])
})

test('имя из справочника подставляется по касанию', async () => {
  await prodolzhit({ supplier: 'Метро', category: 'Сыры', step: 'name' })
  const pole = screen.getByRole('textbox', { name: 'Название' })

  await userEvent.type(pole, 'Моцарела')
  // Взяв имя из справочника, карточка склеится с позицией сама.
  await userEvent.click(await screen.findByRole('button', { name: 'Моцарелла фиор ди латте' }))
  expect(pole).toHaveValue('Моцарелла фиор ди латте')

  const dalee = screen.getByRole('button', { name: 'Далее' })
  // «Далее» ждёт проверки нового названия — дубль ли оно.
  await waitFor(() => expect(dalee).toBeEnabled())
  await userEvent.click(dalee)

  expect(await screen.findByText('Шаг 4 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ name: 'Моцарелла фиор ди латте', step: 'label' }])
})

test('каждый шаг шлёт PATCH с step — и вперёд, и назад', async () => {
  narisovat()
  await userEvent.click(await screen.findByRole('button', { name: 'Начать' }))

  await userEvent.type(await screen.findByRole('textbox', { name: 'Поставщик' }), 'Метро')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  await userEvent.click(await screen.findByRole('radio', { name: 'Сыры' }))
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  await screen.findByText('Шаг 3 из 9')
  await userEvent.click(screen.getByRole('button', { name: 'Назад' }))

  // Вернулся — выбранное на месте.
  expect(await screen.findByRole('radio', { name: 'Сыры' })).toBeChecked()
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  await userEvent.type(await screen.findByRole('textbox', { name: 'Название' }), 'Моцарелла')
  const dalee = screen.getByRole('button', { name: 'Далее' })
  await waitFor(() => expect(dalee).toBeEnabled())
  await userEvent.click(dalee)

  expect(await screen.findByText('Шаг 4 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([
    { supplier: 'Метро', step: 'category' },
    { category: 'Сыры', step: 'name' },
    { step: 'category' },
    { category: 'Сыры', step: 'name' },
    { name: 'Моцарелла', step: 'label' },
  ])
  // Защита от подделки — на каждой правке.
  expect(zashchita).toEqual(['1', '1', '1', '1', '1'])
})

test('перечитывание, начатое до правки, не возвращает экран на прежний шаг', async () => {
  const { queries } = await prodolzhit({ step: 'supplier' })
  // Повар вернулся во вкладку — черновик перечитывается, а сеть медленная:
  // ответ несёт черновик, каким он был до правки.
  server.use(
    http.get('/api/cards/drafts/current', async () => {
      const kakBylo = chernovik
      await delay(400)
      return HttpResponse.json(kakBylo)
    }),
  )
  void queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })

  await userEvent.type(screen.getByRole('textbox', { name: 'Поставщик' }), 'Метро')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))
  expect(await screen.findByText('Шаг 2 из 9')).toBeInTheDocument()

  // Опоздавший ответ пришёл бы теперь — экран остаётся на шаге 2.
  await new Promise((gotovo) => setTimeout(gotovo, 600))
  expect(screen.getByText('Шаг 2 из 9')).toBeInTheDocument()
})

test('черновик пропал (отправлен или сброшен в другой вкладке) — экран перечитывает его', async () => {
  await prodolzhit({ step: 'supplier' })
  chernovik = null

  await userEvent.type(screen.getByRole('textbox', { name: 'Поставщик' }), 'Метро')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  // Правка ответила 404 — вместо тупика с «обновите страницу» экран сам
  // узнаёт, что черновика больше нет, и предлагает начать.
  expect(await screen.findByRole('button', { name: 'Начать' })).toBeInTheDocument()
})
