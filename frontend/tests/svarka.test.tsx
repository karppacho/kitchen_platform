import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import type {
  Ingredient,
  Reconciliation as Svodka,
  ReferenceForm,
  ReferenceRowCurrent,
  ReferenceRowPreview,
  ReferenceTransfer,
} from '../src/api/types'
import { Reconciliation } from '../src/pages/Reconciliation'

/*
 * «Сверка» как список дел: «Это он» и «Это новый» у спорных пар, «Добавить в
 * справочник» у карточки без пары, форма переноса в лист ING. Ответы ручек —
 * из раздела «Интерфейсы для задачи 6» отчёта задачи 5.
 */

const NE_SOGLASOVANA = 'Карточка не согласована — в справочник попадают только «Да»'
const NE_POYAVILAS =
  'Строка ещё не появилась в справочнике — таблица подтягивает карточки с задержкой, ' +
  'попробуйте через несколько минут'
const NE_KANDIDAT = 'Этого ингредиента нет среди кандидатов карточки — обновите страницу'
const SDVINUTY =
  'Строки справочника сдвинуты относительно карточек — запись не сделана, проверьте лист ING'
const OSHIBKA_IMENI = 'Короткое имя для iiko — обязательно'
const OSHIBKA_EDINITSY = 'Единица измерения — кг, л или шт'
const NE_PERENESEN =
  'Ингредиент записан в справочник, но на сайт пока не перенесён — появится при следующем ' +
  'обновлении, через несколько минут'
const ZAPISANO = 'Записано в справочник: строка 8, id 131'

const POTERI = [
  'Потери при перетарке и дефросте, %',
  'Потери при нарезке, %',
  'Потери при тепловой обработке, %',
] as const

function nachalnayaSvodka(): Svodka {
  return {
    total: 10,
    linked: 6,
    needs_human: 4,
    rows: [
      {
        card_id: 6,
        name: 'Сахар',
        link_status: 'ambiguous',
        supplier: 'Метро',
        approved: true,
        candidates: [
          { ingredient_id: 3, legacy_id: '98', name: 'Сахар', score: null },
          { ingredient_id: 4, legacy_id: '99', name: 'Сахар', score: null },
        ],
        actions: ['confirm', 'to_reference'],
      },
      {
        card_id: 9,
        name: 'Томат',
        link_status: 'candidate',
        supplier: '',
        approved: false,
        candidates: [{ ingredient_id: 1, legacy_id: '1', name: 'Томаты', score: 0.909 }],
        actions: ['confirm'],
      },
      {
        card_id: 7,
        name: 'Соус Сырный',
        link_status: 'orphan',
        supplier: '',
        approved: true,
        candidates: [],
        actions: ['to_reference'],
      },
      {
        card_id: 5,
        name: 'Майонез',
        link_status: 'orphan',
        supplier: '',
        approved: false,
        candidates: [],
        actions: [],
      },
    ],
  }
}

function ingredient(id: number, legacyId: string, name: string, unit: string): Ingredient {
  return {
    id,
    legacy_id: legacyId,
    name,
    category: 'Бакалея',
    unit,
    status: 'активный',
    price_per_kg: '85.00',
    weight_per_piece_g: unit === 'шт' ? '5.000' : null,
    has_card: false,
  }
}

const INGREDIENTY = [
  ingredient(3, '98', 'Сахар', 'кг'),
  ingredient(4, '99', 'Сахар', 'шт'),
  ingredient(1, '1', 'Томаты', 'кг'),
]

const TEKUSHCHIE: ReferenceRowCurrent = {
  short_name: '',
  unit: 'кг',
  status: 'активный',
  price_per_kg: '0',
  price_per_pack: null,
  weight_per_piece_g: null,
  losses_unpacking: null,
  losses_cutting: null,
  losses_thermal: null,
}

/** Строка ING карточки «Соус Сырный»: ждёт переноса, L — формула. */
function stroka(chastichno: Partial<ReferenceRowPreview> = {}): ReferenceRowPreview {
  return {
    next_id: '131',
    row: 8,
    ready: true,
    reason: null,
    message: null,
    ref_id: null,
    formulas: ['L', 'P'],
    pulled: {
      category: 'Соусы',
      name: 'Соус Сырный',
      full_name: 'Соус сырный, полное наименование',
      manufacturer: 'Завод «Пример»',
      composition: 'сыр, сливки',
      protein: '1.5',
      fat: '12.5',
      carbs: '3',
      kcal: '150',
    },
    current: TEKUSHCHIE,
    ...chastichno,
  }
}

type Otvet = () => Response | undefined | Promise<Response | undefined>
type Zapros = { cardId: number; telo: unknown; zashchita: string | null }

// Что «сервер» знает и что к нему приходило — заново на каждый тест.
let svodka: Svodka
let chteniyaSvodki: number
let predprosmotry: number[]
let podtverzhdeniya: Zapros[]
let perenosy: Zapros[]
/** Ответы по одному на запрос; кончились — обычный ответ «сервера». */
let otvetyPredprosmotra: Otvet[]
let otvetyPodtverzhdeniya: Otvet[]
let otvetyPerenosa: Otvet[]

/** Карточка ушла со «Сверки»: пара подтверждена. */
function ubrat(cardId: number) {
  svodka = {
    ...svodka,
    linked: svodka.linked + 1,
    needs_human: svodka.needs_human - 1,
    rows: svodka.rows.filter((r) => r.card_id !== cardId),
  }
}

function zapisano(cardId: number, chastichno: Partial<ReferenceTransfer> = {}): Response {
  ubrat(cardId)
  const otvet: ReferenceTransfer = {
    row: 8,
    ref_id: '131',
    already: false,
    imported: true,
    linked: true,
    ingredient_id: 12,
    message: ZAPISANO,
    shifted: null,
    notes: [],
    ...chastichno,
  }
  return HttpResponse.json(otvet)
}

const server = setupServer(
  http.get('/api/reconciliation', () => {
    chteniyaSvodki += 1
    return HttpResponse.json(svodka)
  }),
  http.get('/api/ingredients', () => HttpResponse.json(INGREDIENTY)),
  http.get('/api/reconciliation/:id/reference-row', async ({ params }) => {
    predprosmotry.push(Number(params.id))
    const svoi = await otvetyPredprosmotra.shift()?.()
    return svoi ?? HttpResponse.json(stroka())
  }),
  http.post('/api/reconciliation/:id/confirm', async ({ request, params }) => {
    const cardId = Number(params.id)
    const telo = (await request.json()) as { ingredient_id: number }
    podtverzhdeniya.push({ cardId, telo, zashchita: request.headers.get('X-Kitchen-Csrf') })
    const svoi = await otvetyPodtverzhdeniya.shift()?.()
    if (svoi) return svoi
    const kandidat = svodka.rows
      .find((r) => r.card_id === cardId)!
      .candidates.find((c) => c.ingredient_id === telo.ingredient_id)!
    ubrat(cardId)
    return HttpResponse.json({
      card_id: cardId,
      ingredient_id: kandidat.ingredient_id,
      legacy_id: kandidat.legacy_id,
      name: kandidat.name,
      already: false,
      message: `Пара подтверждена: ингредиент «${kandidat.name}», id ${kandidat.legacy_id}`,
    })
  }),
  http.post('/api/reconciliation/:id/to-reference', async ({ request, params }) => {
    const cardId = Number(params.id)
    perenosy.push({
      cardId,
      telo: await request.json(),
      zashchita: request.headers.get('X-Kitchen-Csrf'),
    })
    const svoi = await otvetyPerenosa.shift()?.()
    return svoi ?? zapisano(cardId)
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
beforeEach(() => {
  svodka = nachalnayaSvodka()
  chteniyaSvodki = 0
  predprosmotry = []
  podtverzhdeniya = []
  perenosy = []
  otvetyPredprosmotra = []
  otvetyPodtverzhdeniya = []
  otvetyPerenosa = []
})
afterEach(() => {
  server.resetHandlers()
  vi.useRealTimers()
})
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const vid = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter future={{ v7_startTransition: false, v7_relativeSplatPath: false }}>
        <Reconciliation />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...vid, queries }
}

/** Часы теста: идут сами, но их можно перевести вперёд — на срок записи. */
function chasy() {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  return {
    polzovatel: userEvent.setup({ advanceTimers: vi.advanceTimersByTime }),
    vperyod: (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms)),
  }
}

/** Ответ, который держит тест: отпускается, когда тесту нужно. */
function otlozhennyi() {
  let otpustit: () => void = () => {}
  const zhdat = new Promise<void>((gotovo) => {
    otpustit = gotovo
  })
  return { zhdat, otpustit }
}

/** Открыть форму переноса у карточки и дождаться строки справочника. */
async function otkrytFormu(
  cardId = 7,
  knopka = 'Добавить в справочник',
  polzovatel: Pick<ReturnType<typeof userEvent.setup>, 'click'> = userEvent,
) {
  const kartochka = await screen.findByTestId(`kartochka-${cardId}`)
  await polzovatel.click(within(kartochka).getByRole('button', { name: knopka }))
  const forma = await screen.findByRole('dialog', { name: /в справочник/i })
  await waitFor(() => expect(within(forma).queryByText(/Читаем строку/)).not.toBeInTheDocument())
  return forma
}

function pole(forma: HTMLElement, nazvanie: string): HTMLElement {
  return within(forma).getByLabelText(nazvanie)
}

function zapisat(forma: HTMLElement): HTMLElement {
  return within(forma).getByRole('button', { name: 'Записать в справочник' })
}

// ---------------------------------------------------------------------------
// Действия у строк
// ---------------------------------------------------------------------------

test('у спорных пар — «Это он» и «Это новый»; несогласованной — только «Это он» и пояснение', async () => {
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  expect(within(sakhar).getByRole('button', { name: 'Это он' })).toBeInTheDocument()
  expect(within(sakhar).getByRole('button', { name: 'Это новый' })).toBeEnabled()
  expect(within(sakhar).queryByText(NE_SOGLASOVANA)).not.toBeInTheDocument()

  const tomat = screen.getByTestId('kartochka-9')
  expect(within(tomat).getByRole('button', { name: 'Это он: «Томаты»' })).toBeInTheDocument()
  expect(within(tomat).queryByRole('button', { name: 'Это новый' })).not.toBeInTheDocument()
  expect(within(tomat).getByText(NE_SOGLASOVANA)).toBeInTheDocument()
})

test('кандидат один — выбирать не из чего: «Это он: «Томаты»» сразу подтверждает пару', async () => {
  // Живая проверка 02.10: кружок у единственного варианта непонятен —
  // кнопка сама называет, с чем связать карточку.
  svodka.rows[1] = { ...svodka.rows[1]!, approved: true, actions: ['confirm', 'to_reference'] }
  narisovat()
  const tomat = await screen.findByTestId('kartochka-9')
  expect(within(tomat).queryByRole('radio')).not.toBeInTheDocument()
  // С чем связывается — видно рядом: имя справочника, id и цена.
  expect(within(tomat).getByText('id 1')).toBeInTheDocument()
  expect(within(tomat).getByText('85,00 ₽/кг')).toBeInTheDocument()
  expect(within(tomat).getByRole('button', { name: 'Это новый' })).toBeEnabled()

  await userEvent.click(within(tomat).getByRole('button', { name: 'Это он: «Томаты»' }))

  expect(
    await screen.findByText('Пара подтверждена: ингредиент «Томаты», id 1'),
  ).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByTestId('kartochka-9')).not.toBeInTheDocument())
  expect(podtverzhdeniya).toEqual([{ cardId: 9, telo: { ingredient_id: 1 }, zashchita: '1' }])
})

/** Строка варианта, в которой стоит переключатель с этим id. */
function strokaVarianta(kartochka: HTMLElement, legacyId: string): HTMLElement {
  return within(kartochka)
    .getByRole('radio', { name: new RegExp(`id ${legacyId}\\b`) })
    .closest('label')!
}

test('кандидатов несколько — нажатие на строку варианта выбирает его, затем «Это он»', async () => {
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  const etoOn = within(sakhar).getByRole('button', { name: 'Это он' })
  expect(etoOn).toBeDisabled()

  // Нажимают не в кружок, а в любое место строки — например, в цену.
  await userEvent.click(within(sakhar).getByText('85,00 ₽/шт'))

  // Программа чтения экрана слышит выбор: переключатель отмечен.
  expect(within(sakhar).getByRole('radio', { name: /id 99\b/ })).toBeChecked()
  expect(within(sakhar).getByRole('radio', { name: /id 98\b/ })).not.toBeChecked()
  // Глаз видит его словами, а не только цветом рамки.
  expect(within(strokaVarianta(sakhar, '99')).getByText('выбран')).toBeInTheDocument()
  expect(within(strokaVarianta(sakhar, '98')).queryByText('выбран')).not.toBeInTheDocument()

  expect(etoOn).toBeEnabled()
  await userEvent.click(etoOn)
  await waitFor(() => expect(screen.queryByTestId('kartochka-6')).not.toBeInTheDocument())
  expect(podtverzhdeniya).toEqual([{ cardId: 6, telo: { ingredient_id: 4 }, zashchita: '1' }])
})

test('варианты выбираются с клавиатуры: группа подписана, стрелки, пробел, Enter', async () => {
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  // Подпись с именем карточки: на «Сверке» групп вариантов несколько.
  expect(within(sakhar).getByRole('radiogroup', { name: 'Варианты для «Сахар»' })).toBeInTheDocument()
  const [pervyi, vtoroi] = within(sakhar).getAllByRole('radio')

  act(() => pervyi!.focus())
  await userEvent.keyboard(' ')
  expect(pervyi).toBeChecked()
  await userEvent.keyboard('{ArrowDown}')
  expect(vtoroi).toBeChecked()
  expect(vtoroi).toHaveFocus()
  expect(pervyi).not.toBeChecked()

  await userEvent.tab()
  const etoOn = within(sakhar).getByRole('button', { name: 'Это он' })
  expect(etoOn).toHaveFocus()
  await userEvent.keyboard('{Enter}')
  await waitFor(() => expect(podtverzhdeniya).toHaveLength(1))
  expect(podtverzhdeniya[0]!.telo).toEqual({ ingredient_id: 4 })
})

/** Стили экрана — те же файлы, что берёт сборка: jsdom считает по ним
 *  getComputedStyle. Вернёт уборку. */
function podklyuchitStili(): () => void {
  const faily = [
    'pages/pages.css',
    'pages/sverka.css',
    'pages/svarka/formaSpravochnika.css',
    'pages/svarka/kandidaty.css',
  ]
  const stili = faily.map((fail) => {
    const stil = document.createElement('style')
    stil.textContent = readFileSync(join(__dirname, '../src', fail), 'utf8')
    document.head.appendChild(stil)
    return stil
  })
  return () => stili.forEach((stil) => stil.remove())
}

test('кнопки и строки вариантов — от 44 px: в них попадают пальцем', async () => {
  const ubratStili = podklyuchitStili()
  try {
    svodka.rows[1] = { ...svodka.rows[1]!, approved: true, actions: ['confirm', 'to_reference'] }
    narisovat()
    const sakhar = await screen.findByTestId('kartochka-6')
    const tomat = screen.getByTestId('kartochka-9')
    const tseli = [
      within(sakhar).getByRole('button', { name: 'Это он' }),
      within(sakhar).getByRole('button', { name: 'Это новый' }),
      strokaVarianta(sakhar, '98'),
      strokaVarianta(sakhar, '99'),
      within(tomat).getByRole('button', { name: 'Это он: «Томаты»' }),
      within(tomat).getByRole('button', { name: 'Это новый' }),
    ]
    for (const tsel of tseli) expect(getComputedStyle(tsel).minHeight).toBe('44px')
  } finally {
    ubratStili()
  }
})

test('«Это он» — выбор кандидата: пара подтверждается, карточка уходит со «Сверки»', async () => {
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  const etoOn = within(sakhar).getByRole('button', { name: 'Это он' })
  // Предвыбранного варианта нет — подтверждать нечего, пока человек не выбрал.
  expect(etoOn).toBeDisabled()

  await userEvent.click(within(sakhar).getAllByRole('radio')[1]!)
  await userEvent.click(etoOn)

  expect(
    await screen.findByText('Пара подтверждена: ингредиент «Сахар», id 99'),
  ).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByTestId('kartochka-6')).not.toBeInTheDocument())
  expect(podtverzhdeniya).toEqual([{ cardId: 6, telo: { ingredient_id: 4 }, zashchita: '1' }])
})

test('«Это он» с устаревшим кандидатом (409) — текст сервера, список перечитан', async () => {
  otvetyPodtverzhdeniya = [
    () =>
      HttpResponse.json(
        { detail: NE_KANDIDAT, reason: 'not_candidate', row: null },
        { status: 409 },
      ),
  ]
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  await userEvent.click(within(sakhar).getAllByRole('radio')[0]!)
  await userEvent.click(within(sakhar).getByRole('button', { name: 'Это он' }))

  expect(await within(sakhar).findByRole('alert')).toHaveTextContent(NE_KANDIDAT)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
})

test('у карточки без пары и «Да» — «Добавить в справочник»', async () => {
  narisovat()
  const sous = await screen.findByTestId('kartochka-7')
  expect(within(sous).getByRole('button', { name: 'Добавить в справочник' })).toBeEnabled()
  expect(within(sous).queryByText(NE_SOGLASOVANA)).not.toBeInTheDocument()
})

test('у карточки не «Да» — пояснение без кнопки', async () => {
  narisovat()
  const mayonez = await screen.findByTestId('kartochka-5')
  expect(within(mayonez).getByText(NE_SOGLASOVANA)).toBeInTheDocument()
  expect(within(mayonez).queryByRole('button')).not.toBeInTheDocument()
})

test('«Это новый» у спорной пары открывает ту же форму переноса', async () => {
  narisovat()
  const forma = await otkrytFormu(6, 'Это новый')
  expect(within(forma).getByText('Сахар')).toBeInTheDocument()
  expect(predprosmotry).toEqual([6])
})

// ---------------------------------------------------------------------------
// Форма
// ---------------------------------------------------------------------------

test('форма: подтянутое — только для чтения, id показан, L скрыт с пометкой, если формула', async () => {
  narisovat()
  const forma = await otkrytFormu()

  expect(predprosmotry).toEqual([7])
  // Подтянутое выводит формула таблицы — показывается текстом, не полями.
  for (const tekst of ['Соусы', 'Соус сырный, полное наименование', 'Завод «Пример»', 'сыр, сливки']) {
    expect(within(forma).getByText(tekst)).toBeInTheDocument()
  }
  expect(within(forma).queryByRole('textbox', { name: /Категория|Изготовитель|Состав/ })).toBe(
    null,
  )
  // id выдаёт сервер при записи; человек его видит, но не вводит.
  expect(within(forma).getByText(/будет 131/)).toBeInTheDocument()
  expect(within(forma).queryByRole('textbox', { name: /^id/ })).not.toBeInTheDocument()
  // L — формула от M: поля нет, есть пометка.
  expect(within(forma).queryByLabelText(/Цена за 1 кг/)).not.toBeInTheDocument()
  expect(within(forma).getByText(/считается в таблице/)).toBeInTheDocument()
  expect(pole(forma, 'Цена за упаковку, ₽')).toBeInTheDocument()
})

test('форма: статус «активный» виден, но не поле — его ставит запись', async () => {
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByText('активный (ставится при записи)')).toBeInTheDocument()
  expect(within(forma).queryByRole('textbox', { name: /Статус/ })).not.toBeInTheDocument()
})

test('форма: L без формулы — поле цены за единицу есть', async () => {
  otvetyPredprosmotra = [() => HttpResponse.json(stroka({ formulas: ['P'] }))]
  narisovat()
  const forma = await otkrytFormu()
  expect(pole(forma, 'Цена за 1 кг (или 1 шт / 1 л), ₽')).toBeInTheDocument()
  expect(within(forma).queryByText(/считается в таблице/)).not.toBeInTheDocument()
})

test('форма: вес 1 шт — только для «шт»', async () => {
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByRole('radio', { name: 'кг' })).toBeChecked()
  expect(within(forma).queryByLabelText('Вес 1 шт, г')).not.toBeInTheDocument()

  await userEvent.click(within(forma).getByRole('radio', { name: 'шт' }))
  expect(pole(forma, 'Вес 1 шт, г')).toBeInTheDocument()

  await userEvent.click(within(forma).getByRole('radio', { name: 'л' }))
  expect(within(forma).queryByLabelText('Вес 1 шт, г')).not.toBeInTheDocument()
})

test('форма: потери по умолчанию 0, итог считается для сверки; 100 % — подсказка', async () => {
  narisovat()
  const forma = await otkrytFormu()
  for (const nazvanie of POTERI) expect(pole(forma, nazvanie)).toHaveValue('0')
  const itog = within(forma).getByText(/Общие потери/)
  expect(itog).toHaveTextContent('Общие потери: 0 %')

  await userEvent.clear(pole(forma, POTERI[0]))
  await userEvent.type(pole(forma, POTERI[0]), '5')
  await userEvent.clear(pole(forma, POTERI[1]))
  await userEvent.type(pole(forma, POTERI[1]), '12,5')
  expect(itog).toHaveTextContent('Общие потери: 17,5 %')

  // Каждая потеря — от 0 до 99,99 %, тем же текстом, что у сервера. Это
  // подсказка, решает сервер.
  expect(within(forma).queryByText(/от 0 до 99,99 %/)).not.toBeInTheDocument()
  await userEvent.clear(pole(forma, POTERI[2]))
  await userEvent.type(pole(forma, POTERI[2]), '100')
  expect(pole(forma, POTERI[2])).toHaveAccessibleDescription('от 0 до 99,99 %')
  await userEvent.clear(pole(forma, POTERI[2]))
  await userEvent.type(pole(forma, POTERI[2]), 'пять')
  expect(pole(forma, POTERI[2])).toHaveAccessibleDescription('введите число, например 5 или 12,5')
  expect(zapisat(forma)).toBeEnabled()
})

const SUMMA_VNE = 'Сумма потерь 100 % и больше — проверьте цифры'

test('форма: сумма потерь 100 % и больше — подсказка у итога, решает человек', async () => {
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.clear(pole(forma, POTERI[0]))
  await userEvent.type(pole(forma, POTERI[0]), '60')
  expect(within(forma).queryByText(SUMMA_VNE)).not.toBeInTheDocument()

  // Каждая меньше 100 %, а вместе — 100 %.
  await userEvent.clear(pole(forma, POTERI[1]))
  await userEvent.type(pole(forma, POTERI[1]), '40')

  expect(within(forma).getByText(/Общие потери/)).toHaveTextContent('Общие потери: 100 %')
  expect(within(forma).getByText(SUMMA_VNE)).toBeInTheDocument()
  expect(zapisat(forma)).toBeEnabled()
})

test('форма: под ценами сказано, что пустая цена ячейку таблицы не меняет', async () => {
  otvetyPredprosmotra = [() => HttpResponse.json(stroka({ formulas: ['P'] }))]
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByText('Пустая цена не меняет ячейку таблицы')).toBeInTheDocument()
})

test('предпросмотр не ответил за 60 с — «не ответил вовремя», а не «нет связи»', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyPredprosmotra = [
    async () => {
      await delay('infinite')
      return undefined
    },
  ]
  narisovat()
  const kartochka = await screen.findByTestId('kartochka-7')
  await polzovatel.click(within(kartochka).getByRole('button', { name: 'Добавить в справочник' }))
  const forma = await screen.findByRole('dialog', { name: /в справочник/i })

  await vperyod(60_000)

  expect(await within(forma).findByRole('alert')).toHaveTextContent(
    'Сервер не ответил вовремя — проверьте связь и нажмите «Проверить ещё раз».',
  )
})

test('форма начинается с текущих значений строки — заготовки шефа', async () => {
  otvetyPredprosmotra = [
    () =>
      HttpResponse.json(
        stroka({
          formulas: ['P'],
          current: {
            short_name: 'Сыр соус',
            unit: 'шт',
            status: 'активный',
            price_per_kg: '41.5',
            price_per_pack: '1250.5',
            weight_per_piece_g: '25',
            losses_unpacking: '0',
            losses_cutting: '12.5',
            losses_thermal: null,
          },
        }),
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сыр соус')
  expect(within(forma).getByRole('radio', { name: 'шт' })).toBeChecked()
  expect(pole(forma, 'Цена за 1 кг (или 1 шт / 1 л), ₽')).toHaveValue('41,5')
  expect(pole(forma, 'Цена за упаковку, ₽')).toHaveValue('1250,5')
  expect(pole(forma, 'Вес 1 шт, г')).toHaveValue('25')
  expect(pole(forma, POTERI[1])).toHaveValue('12,5')
  expect(pole(forma, POTERI[2])).toHaveValue('0')
  expect(within(forma).getByText(/Общие потери/)).toHaveTextContent('Общие потери: 12,5 %')
})

test('«Закрыть» — форма уходит, фокус возвращается к кнопке', async () => {
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.click(within(forma).getByRole('button', { name: 'Закрыть' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  const sous = screen.getByTestId('kartochka-7')
  expect(within(sous).getByRole('button', { name: 'Добавить в справочник' })).toHaveFocus()
})

/** Предпросмотр: строки ещё нет — таблица не подтянула карточку. */
function neNaidena(): ReferenceRowPreview {
  return stroka({
    row: null,
    ready: false,
    reason: 'not_yet',
    message: NE_POYAVILAS,
    formulas: [],
    pulled: null,
    current: null,
  })
}

const SNACHALA_PROVERIM = 'Сначала проверим строку — покажем, что в ней сейчас'

test('строка ещё не появилась (предпросмотр) — ни полей, ни «Записать»: сначала проверить строку', async () => {
  // Писать в строку, которую человек не видел, нельзя: таблица могла её уже
  // подтянуть, и пустые поля оставили бы в ней умолчания заготовки, а L
  // показан, хотя в строке он, может быть, формула.
  otvetyPredprosmotra = [() => HttpResponse.json(neNaidena())]
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByText(NE_POYAVILAS)).toBeInTheDocument()
  expect(within(forma).getByText(SNACHALA_PROVERIM)).toBeInTheDocument()
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)

  await userEvent.click(within(forma).getByRole('button', { name: 'Проверить ещё раз' }))

  // Строка нашлась — форма с тем, что в ней сейчас; L — формула: поля нет.
  expect(await within(forma).findByText('Соусы')).toBeInTheDocument()
  expect(within(forma).queryByText(NE_POYAVILAS)).not.toBeInTheDocument()
  expect(within(forma).queryByText(SNACHALA_PROVERIM)).not.toBeInTheDocument()
  expect(predprosmotry).toEqual([7, 7])
  expect(within(forma).getByRole('radio', { name: 'кг' })).toBeChecked()
  expect(within(forma).queryByLabelText(/Цена за 1 кг/)).not.toBeInTheDocument()
  expect(zapisat(forma)).toBeEnabled()
})

test('предпросмотр отказал (503) — текст сервера, формы нет', async () => {
  otvetyPredprosmotra = [
    () =>
      HttpResponse.json(
        { detail: 'Колонки справочника изменились — сообщите разработчику' },
        { status: 503 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByRole('alert')).toHaveTextContent(
    'Колонки справочника изменились — сообщите разработчику',
  )
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)
})

// ---------------------------------------------------------------------------
// Запись
// ---------------------------------------------------------------------------

test('успех — «Записано в справочник: строка N, id X», карточка уходит, справочник перечитан', async () => {
  const { queries } = narisovat()
  const perechityvaniya = vi.spyOn(queries, 'invalidateQueries')
  const forma = await otkrytFormu()

  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.type(pole(forma, 'Цена за упаковку, ₽'), '1250,5')
  await userEvent.clear(pole(forma, POTERI[0]))
  await userEvent.type(pole(forma, POTERI[0]), '5')
  await userEvent.click(zapisat(forma))

  const itog = await screen.findByText(ZAPISANO)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  await waitFor(() => expect(screen.queryByTestId('kartochka-7')).not.toBeInTheDocument())
  expect(perechityvaniya).toHaveBeenCalledWith({ queryKey: ['reconciliation'] })
  expect(perechityvaniya).toHaveBeenCalledWith({ queryKey: ['ingredients'] })
  // Кнопка, на которой был фокус, ушла вместе с формой — фокус на итог.
  expect(itog.closest('[tabindex="-1"]')).toHaveFocus()
  // L — формула: цену за единицу не шлём; вес — только у «шт»; статус и id —
  // не поля формы.
  expect(perenosy).toEqual([
    {
      cardId: 7,
      zashchita: '1',
      telo: {
        short_name: 'Сырный',
        unit: 'кг',
        price_per_pack: '1250,5',
        weight_per_piece_g: null,
        losses_unpacking: '5',
        losses_cutting: '0',
        losses_thermal: '0',
      },
    },
  ])
})

test('оговорки сервера после записи — списком, как есть', async () => {
  otvetyPerenosa = [() => zapisano(7, { imported: false, notes: [NE_PERENESEN] })]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await screen.findByText(ZAPISANO)).toBeInTheDocument()
  expect(screen.getByText(NE_PERENESEN)).toBeInTheDocument()
})

test('422 — ошибка у поля, по-русски; фокус на первом поле с ошибкой', async () => {
  otvetyPredprosmotra = [() => HttpResponse.json(stroka({ current: { ...TEKUSHCHIE, unit: 'уп' } }))]
  otvetyPerenosa = [
    () =>
      HttpResponse.json(
        {
          detail: `${OSHIBKA_IMENI}; ${OSHIBKA_EDINITSY}`,
          errors: { short_name: OSHIBKA_IMENI, unit: OSHIBKA_EDINITSY },
        },
        { status: 422 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  // Единицы «уп» среди кг, л, шт нет — выбирает человек.
  for (const variant of within(forma).getAllByRole('radio')) expect(variant).not.toBeChecked()

  await userEvent.click(zapisat(forma))

  const imya = pole(forma, 'Короткое имя для iiko')
  await waitFor(() => expect(imya).toHaveAttribute('aria-invalid', 'true'))
  expect(imya).toHaveAccessibleDescription(OSHIBKA_IMENI)
  expect(within(forma).getByRole('group', { name: 'Единица измерения' })).toHaveAccessibleDescription(
    OSHIBKA_EDINITSY,
  )
  expect(imya).toHaveFocus()
  expect(screen.getByRole('dialog')).toBe(forma)
})

test('422 испорченного запроса (список FastAPI) — общий текст, экран не падает', async () => {
  otvetyPerenosa = [
    () =>
      HttpResponse.json(
        { detail: [{ loc: ['body', 'status'], msg: 'Extra inputs are not permitted' }] },
        { status: 422 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(/обновите страницу/)
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
})

test('409 «ещё не появилась» — текст, форма с введённым остаётся', async () => {
  otvetyPerenosa = [
    () =>
      HttpResponse.json({ detail: NE_POYAVILAS, reason: 'not_yet', row: null }, { status: 409 }),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.clear(pole(forma, POTERI[0]))
  await userEvent.type(pole(forma, POTERI[0]), '5')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(NE_POYAVILAS)
  expect(screen.getByRole('dialog')).toBe(forma)
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
  expect(pole(forma, POTERI[0])).toHaveValue('5')
  // Строку человек уже видел — предпросмотр был готов, в полях её значения и
  // его правки: записать можно сразу, а можно сначала проверить.
  expect(zapisat(forma)).toBeEnabled()
  expect(within(forma).getByRole('button', { name: 'Проверить ещё раз' })).toBeEnabled()
})

test('правки переживают «ещё не появилась»: строка нашлась — форма с набранным', async () => {
  otvetyPerenosa = [
    () =>
      HttpResponse.json({ detail: NE_POYAVILAS, reason: 'not_yet', row: null }, { status: 409 }),
  ]
  otvetyPredprosmotra = [() => HttpResponse.json(stroka()), () => HttpResponse.json(neNaidena())]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))
  expect(await within(forma).findByRole('alert')).toHaveTextContent(NE_POYAVILAS)

  // Проверили — строки нет: полей и «Записать» нет, набранное не забыто.
  await userEvent.click(within(forma).getByRole('button', { name: 'Проверить ещё раз' }))
  expect(await within(forma).findByText(SNACHALA_PROVERIM)).toBeInTheDocument()
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)

  await userEvent.click(within(forma).getByRole('button', { name: 'Проверить ещё раз' }))
  expect(await within(forma).findByLabelText('Короткое имя для iiko')).toHaveValue('Сырный')
  expect(zapisat(forma)).toBeEnabled()
  expect(predprosmotry).toEqual([7, 7, 7])
})

// ---------------------------------------------------------------------------
// Пара уже подтверждена: другой человек успел раньше
// ---------------------------------------------------------------------------

const PARA_PODTVERZHDENA = 'Пара уже подтверждена — обновите страницу'

test('предпросмотр: пара уже подтверждена — без полей, «Записать» и «Проверить ещё раз»', async () => {
  // Проверка строки ничего не изменит: пару уже подтвердил человек.
  otvetyPredprosmotra = [
    () =>
      HttpResponse.json(stroka({ ready: false, reason: 'confirmed', message: PARA_PODTVERZHDENA })),
  ]
  narisovat()
  const forma = await otkrytFormu()
  expect(within(forma).getByText(PARA_PODTVERZHDENA)).toBeInTheDocument()
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)
  expect(within(forma).queryByRole('button', { name: 'Проверить ещё раз' })).toBe(null)
})

test('запись: пара уже подтверждена (409) — текст, список перечитан, поля остаются, кнопок нет', async () => {
  otvetyPerenosa = [
    () =>
      HttpResponse.json(
        { detail: PARA_PODTVERZHDENA, reason: 'confirmed', row: 8 },
        { status: 409 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(PARA_PODTVERZHDENA)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
  expect(zapisat(forma)).toBeDisabled()
  expect(within(forma).queryByRole('button', { name: 'Проверить ещё раз' })).toBe(null)
})

test('«Это он»: пара уже подтверждена другим (409) — текст сервера, список перечитан', async () => {
  otvetyPodtverzhdeniya = [
    () =>
      HttpResponse.json(
        { detail: PARA_PODTVERZHDENA, reason: 'confirmed', row: null },
        { status: 409 },
      ),
  ]
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  await userEvent.click(within(sakhar).getAllByRole('radio')[0]!)
  await userEvent.click(within(sakhar).getByRole('button', { name: 'Это он' }))

  expect(await within(sakhar).findByRole('alert')).toHaveTextContent(PARA_PODTVERZHDENA)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
})

// ---------------------------------------------------------------------------
// Сессия истекла: правки идут мимо кэша, вход возвращает перечитанный список
// ---------------------------------------------------------------------------

const VOIDITE = 'Войдите заново'

function sessiyaIstekla() {
  server.use(
    http.post('/api/auth/refresh', () => HttpResponse.json({ detail: VOIDITE }, { status: 401 })),
  )
}

test('«Это он» с истёкшей сессией (401) — список «Сверки» перечитан', async () => {
  sessiyaIstekla()
  otvetyPodtverzhdeniya = [() => HttpResponse.json({ detail: VOIDITE }, { status: 401 })]
  narisovat()
  const sakhar = await screen.findByTestId('kartochka-6')
  await userEvent.click(within(sakhar).getAllByRole('radio')[0]!)
  await userEvent.click(within(sakhar).getByRole('button', { name: 'Это он' }))

  expect(await within(sakhar).findByRole('alert')).toHaveTextContent(VOIDITE)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
})

test('запись с истёкшей сессией (401) — список «Сверки» перечитан, набранное цело', async () => {
  sessiyaIstekla()
  otvetyPerenosa = [() => HttpResponse.json({ detail: VOIDITE }, { status: 401 })]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(VOIDITE)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
})

test('запись отказала «строки сдвинуты» (409) — «Записать» ждёт «Проверить ещё раз»', async () => {
  // Повтор записи дал бы тот же отказ: сначала шеф правит лист, потом —
  // свежая проверка строки.
  otvetyPerenosa = [
    () => HttpResponse.json({ detail: SDVINUTY, reason: 'shifted', row: 8 }, { status: 409 }),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(SDVINUTY)
  expect(zapisat(forma)).toBeDisabled()

  await userEvent.click(within(forma).getByRole('button', { name: 'Проверить ещё раз' }))

  await waitFor(() => expect(zapisat(forma)).toBeEnabled())
  expect(within(forma).queryByRole('alert')).not.toBeInTheDocument()
  expect(predprosmotry).toEqual([7, 7])
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
})

test('«Проверить ещё раз» не удалась после «нужен человек» — «Записать» так и ждёт', async () => {
  // Перечитывание забывает отказ записи, а данные строки остаются прежними:
  // без проверки отказа чтения «Записать» открылась бы для той же строки.
  otvetyPerenosa = [
    () => HttpResponse.json({ detail: SDVINUTY, reason: 'shifted', row: 8 }, { status: 409 }),
  ]
  otvetyPredprosmotra = [
    () => HttpResponse.json(stroka()),
    () =>
      HttpResponse.json(
        {
          detail:
            'Google-таблица не ответила — в справочнике ничего не изменилось, нажмите ещё раз',
        },
        { status: 502 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))
  expect(await within(forma).findByRole('alert')).toHaveTextContent(SDVINUTY)

  await userEvent.click(within(forma).getByRole('button', { name: 'Проверить ещё раз' }))

  expect(await within(forma).findByText(/Google-таблица не ответила/)).toBeInTheDocument()
  expect(zapisat(forma)).toBeDisabled()
})

test.each([
  [
    502,
    {
      detail: 'Не удалось подтвердить запись — нажмите ещё раз: второй записи не будет',
      row: 8,
      journal_id: 3,
    },
  ],
  [503, { detail: 'Таблица занята — попробуйте ещё раз' }],
])('запись: %i — текст сервера, набранное цело, «Записать» доступна', async (kod, telo) => {
  otvetyPerenosa = [() => HttpResponse.json(telo, { status: kod })]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(telo.detail)
  expect(pole(forma, 'Короткое имя для iiko')).toHaveValue('Сырный')
  // Повтор безопасен: журнал на сервере второй записи не даст.
  expect(zapisat(forma)).toBeEnabled()
  expect(perenosy).toHaveLength(1)
})

test('запись: карточку сняли с согласования (409 «не Да») — формы нет, список перечитан', async () => {
  otvetyPerenosa = [
    () =>
      HttpResponse.json(
        { detail: NE_SOGLASOVANA, reason: 'not_approved', row: null },
        { status: 409 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent(NE_SOGLASOVANA)
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)
  expect(within(forma).queryByRole('button', { name: 'Проверить ещё раз' })).toBe(null)
  await waitFor(() => expect(chteniyaSvodki).toBe(2))
})

test('ответ 200 оборвался посреди тела — повтор, итог «записано… подтверждено повтором»', async () => {
  // Запись легла, а ответ не дошёл целиком — это обрыв, а не отказ. Повтор
  // находит запись по журналу и отвечает «уже», но человек нажал
  // «Записать» один раз: для него это запись, а не чужое «уже в справочнике».
  const { polzovatel, vperyod } = chasy()
  otvetyPerenosa = [
    () =>
      new HttpResponse('{"row": 8, "ref_id": "1', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    () => zapisano(7, { already: true, message: 'Ингредиент уже в справочнике — id 131' }),
  ]
  narisovat()
  const forma = await otkrytFormu(7, 'Добавить в справочник', polzovatel)
  await polzovatel.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await polzovatel.click(zapisat(forma))

  expect(await within(forma).findByText(/попытка 2 из 3/)).toBeInTheDocument()
  await vperyod(2_000)

  expect(
    await screen.findByText('Записано в справочник: строка 8, id 131 (подтверждено повтором)'),
  ).toBeInTheDocument()
  expect(perenosy).toHaveLength(2)
  expect(perenosy[1]).toEqual(perenosy[0])
})

test('422 с пустыми ошибками полей — текст сервера, а не молчание', async () => {
  otvetyPerenosa = [
    () => HttpResponse.json({ detail: 'Форма не принята', errors: {} }, { status: 422 }),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.click(zapisat(forma))

  expect(await within(forma).findByRole('alert')).toHaveTextContent('Форма не принята')
})

test('пока идёт запись, поля — только для чтения: правка не пропадает молча', async () => {
  // Запись идёт до двух минут. Уходит то, что было при нажатии; правка,
  // которую форма приняла бы, в лист не легла бы, а итог значений не
  // показывает.
  const otvet = otlozhennyi()
  otvetyPerenosa = [
    async () => {
      await otvet.zhdat
      return undefined
    },
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await userEvent.type(pole(forma, 'Цена за упаковку, ₽'), '100')
  await userEvent.click(zapisat(forma))
  expect(await within(forma).findByText(/Записываем в справочник/)).toBeInTheDocument()

  const tsena = pole(forma, 'Цена за упаковку, ₽')
  expect(tsena).toHaveAttribute('readonly')
  await userEvent.type(tsena, '5')
  expect(tsena).toHaveValue('100')
  await userEvent.click(within(forma).getByRole('radio', { name: 'шт' }))
  expect(within(forma).getByRole('radio', { name: 'кг' })).toBeChecked()
  expect(within(forma).queryByLabelText('Вес 1 шт, г')).not.toBeInTheDocument()

  otvet.otpustit()

  expect(await screen.findByText(ZAPISANO)).toBeInTheDocument()
  expect(perenosy).toHaveLength(1)
  expect((perenosy[0]!.telo as ReferenceForm).price_per_pack).toBe('100')
})

test('двойное нажатие — один запрос; пока идёт запись, кнопки ждут', async () => {
  const otvet = otlozhennyi()
  otvetyPerenosa = [
    async () => {
      await otvet.zhdat
      return undefined
    },
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')

  // Два нажатия быстрее перерисовки: второе застаёт кнопку ещё доступной.
  const knopka = zapisat(forma)
  act(() => {
    knopka.click()
    knopka.click()
  })

  expect(await within(forma).findByText(/Записываем в справочник/)).toBeInTheDocument()
  expect(zapisat(forma)).toBeDisabled()
  expect(within(forma).getByRole('button', { name: 'Закрыть' })).toBeDisabled()

  otvet.otpustit()

  expect(await screen.findByText(ZAPISANO)).toBeInTheDocument()
  expect(perenosy).toHaveLength(1)
})

test('срок ответа (120 с) вышел — тот же запрос ещё раз: второй записи не будет', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyPerenosa = [
    async () => {
      // Сервер пишет долго, ответ не приходит.
      await delay('infinite')
      return undefined
    },
  ]
  narisovat()
  const forma = await otkrytFormu(7, 'Добавить в справочник', polzovatel)
  await polzovatel.type(pole(forma, 'Короткое имя для iiko'), 'Сырный')
  await polzovatel.click(zapisat(forma))

  expect(await within(forma).findByText(/может занять до двух минут/)).toBeInTheDocument()
  await vperyod(119_000)
  expect(perenosy).toHaveLength(1)

  await vperyod(1_000)
  // Сервер, скорее всего, ещё пишет — не «связь прервалась», а «ответа нет».
  expect(
    await within(forma).findByText(/Ответа пока нет — спрашиваем ещё раз \(попытка 2 из 3\)/),
  ).toBeInTheDocument()
  expect(within(forma).getByText(/второй записи не будет/)).toBeInTheDocument()
  await vperyod(2_000)

  expect(await screen.findByText(ZAPISANO)).toBeInTheDocument()
  expect(perenosy).toHaveLength(2)
  expect(perenosy[1]).toEqual(perenosy[0])
})

// ---------------------------------------------------------------------------
// «Уже в справочнике»
// ---------------------------------------------------------------------------

const UZHE = 'Ингредиент уже в справочнике — id 42'

function uzheVSpravochnike(): ReferenceRowPreview {
  return stroka({
    ready: false,
    reason: 'already',
    message: UZHE,
    ref_id: '42',
    formulas: ['P'],
    current: {
      short_name: 'Сырный',
      unit: 'кг',
      status: 'активный',
      price_per_kg: '410',
      price_per_pack: '410',
      weight_per_piece_g: null,
      losses_unpacking: '0',
      losses_cutting: '12.5',
      losses_thermal: null,
    },
  })
}

test('уже в справочнике — формы нет, «Связать с карточкой» шлёт значения строки', async () => {
  otvetyPredprosmotra = [() => HttpResponse.json(uzheVSpravochnike())]
  otvetyPerenosa = [() => zapisano(7, { ref_id: '42', already: true, message: UZHE })]
  narisovat()
  const forma = await otkrytFormu()

  expect(within(forma).getByText(UZHE)).toBeInTheDocument()
  expect(within(forma).queryByLabelText('Короткое имя для iiko')).not.toBeInTheDocument()
  expect(within(forma).queryByRole('button', { name: 'Записать в справочник' })).toBe(null)

  await userEvent.click(within(forma).getByRole('button', { name: 'Связать с карточкой' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText(UZHE)).toBeInTheDocument()
  expect(perenosy.map((z) => z.telo)).toEqual([
    {
      short_name: 'Сырный',
      unit: 'кг',
      price_per_kg: '410',
      price_per_pack: '410',
      weight_per_piece_g: null,
      losses_unpacking: '0',
      losses_cutting: '12,5',
      losses_thermal: '0',
    },
  ])
})

test('«Связать с карточкой» с повтором после обрыва — итог «уже в справочнике», как и было', async () => {
  // Здесь «уже» — правда и без повтора: ингредиент был в справочнике до
  // нажатия, записи не было.
  const { polzovatel, vperyod } = chasy()
  otvetyPredprosmotra = [() => HttpResponse.json(uzheVSpravochnike())]
  otvetyPerenosa = [
    () => HttpResponse.error(),
    () => zapisano(7, { ref_id: '42', already: true, message: UZHE }),
  ]
  narisovat()
  const forma = await otkrytFormu(7, 'Добавить в справочник', polzovatel)
  await polzovatel.click(within(forma).getByRole('button', { name: 'Связать с карточкой' }))

  expect(await within(forma).findByText(/попытка 2 из 3/)).toBeInTheDocument()
  await vperyod(2_000)

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText(UZHE)).toBeInTheDocument()
  expect(screen.queryByText(/подтверждено повтором/)).not.toBeInTheDocument()
})

test('уже в справочнике, а в строке пусто короткое имя (422) — поля появляются с ошибкой', async () => {
  otvetyPredprosmotra = [
    () =>
      HttpResponse.json({
        ...uzheVSpravochnike(),
        current: { ...uzheVSpravochnike().current!, short_name: '' },
      }),
  ]
  otvetyPerenosa = [
    () =>
      HttpResponse.json(
        { detail: OSHIBKA_IMENI, errors: { short_name: OSHIBKA_IMENI } },
        { status: 422 },
      ),
  ]
  narisovat()
  const forma = await otkrytFormu()
  await userEvent.click(within(forma).getByRole('button', { name: 'Связать с карточкой' }))

  const imya = await within(forma).findByLabelText('Короткое имя для iiko')
  expect(imya).toHaveAccessibleDescription(OSHIBKA_IMENI)
  // Поля появились — сказано зачем: записи в таблицу не будет.
  expect(
    within(forma).getByText(
      'В таблицу эти значения не запишутся — они нужны только, чтобы связать карточку',
    ),
  ).toBeInTheDocument()
  await userEvent.type(imya, 'Сырный')
  await userEvent.click(within(forma).getByRole('button', { name: 'Связать с карточкой' }))

  expect(await screen.findByText(ZAPISANO)).toBeInTheDocument()
  expect(perenosy.map((z) => (z.telo as { short_name: string }).short_name)).toEqual([
    '',
    'Сырный',
  ])
})
