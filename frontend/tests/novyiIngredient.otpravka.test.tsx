import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import { KLYUCH_CHERNOVIKA } from '../src/api/kartochki'
import type { Draft, DraftPatch, Shag, Submitted } from '../src/api/types'
import { App } from '../src/App'
import { podmenitBrauzer, podmenitFormData, snimok } from './podmenaFoto'

const ID = '0b6f3f5e-6a51-4f43-9a52-6f1d1c0e7a01'
const NOVYI = '7c1e2d3f-1b2a-4c5d-8e9f-0a1b2c3d4e5f'
const STROKA = 128

/** Чего не хватает для отправки — так же, как считает сервер. */
function nedostayot(c: Draft): string[] {
  const net: string[] = []
  if (!c.supplier.trim()) net.push('Поставщик')
  if (!c.category.trim()) net.push('Категория')
  if (!c.name.trim()) net.push('Название')
  if (!c.photos.label) net.push('Фото этикетки')
  if (c.approval === null) net.push('Согласован ли продукт')
  return net
}

/** Карточка, заполненная до нужного шага: этикетка распознана, продукт согласован. */
function chernovikNa(step: Shag, chastichno: Partial<Draft> = {}): Draft {
  const c: Draft = {
    id: ID,
    status: 'active',
    step,
    supplier: 'Метро',
    category: 'Сыры',
    name: 'Моцарелла',
    label_name: 'Сыр моцарелла 45%',
    manufacturer: 'Сыроварня «Пример»',
    composition: 'молоко нормализованное, соль, закваска',
    protein: '18',
    fat: '22.5',
    carbs: '1.25',
    kcal: '280',
    shelf_life_sealed: '45 суток',
    shelf_life_defrost: '',
    shelf_life_after: '72 часа',
    defrost_conditions: '',
    description: '',
    approval: 'Да',
    photos: { label: true, package: false, before: false, after: false },
    recognition_status: 'done',
    recognition_error: null,
    warnings: [],
    missing: [],
    created_at: '2026-10-01T08:00:00Z',
    updated_at: '2026-10-01T08:00:00Z',
    ...chastichno,
  }
  return { ...c, missing: nedostayot(c) }
}

const SDVIG =
  'В таблице карточек сдвинулись колонки — запись остановлена, в таблице ничего не изменилось. Черновик сохранён; сообщите шефу.'
const ZANYATA = 'Таблица занята — попробуйте ещё раз через минуту'
const NE_NASTROENA = 'Таблица карточек не настроена — сообщите администратору. Черновик сохранён.'
const GOOGLE_NE_PRINYAL =
  'Google не принял запись — в таблице ничего не изменилось. Черновик сохранён, отправьте ещё раз.'
const NE_PERENESENA =
  'Карточка записана в таблицу, но на сайт пока не перенесена — появится при следующем обновлении, через несколько минут.'

type Otvet = () => Response | undefined | Promise<Response | undefined>

// Что «сервер» знает и что к нему приходило — заново на каждый тест.
let chernovik: Draft | null
let pravki: DraftPatch[]
let zagruzki: { vid: string; foto: string; zashchita: string | null }[]
let otpravki: { id: string; zashchita: string | null }[]
let sozdano: number
/** Ответы по одному на запрос; кончились — обычный ответ «сервера». */
let otvetyZagruzki: Otvet[]
let otvetyOtpravki: ('obryv' | Otvet)[]
let otvetyUdaleniya: Otvet[]
let udaleniya: { vid: string; zashchita: string | null }[]
/** Строка, в которую легла карточка; повтор отправки — та же строка. */
let stroka: number | null
/** Сколько раз «сервер» менял черновик фото — для времени правки. */
let pravokFoto: number

/** Время правки — растёт с каждой правкой фото, с долями секунды, как у базы. */
function vremyaPravki(): string {
  pravokFoto += 1
  return `2026-10-01T09:00:${String(pravokFoto).padStart(2, '0')}.123456Z`
}

/** Карточка в листе: ответ отправки. Повтор — та же строка, второй нет. */
function zapisano(chastichno: Partial<Submitted> = {}): Response {
  const povtor = stroka !== null
  stroka = STROKA
  const imya = chernovik?.name ?? 'Моцарелла'
  chernovik = null
  const otvet: Submitted = {
    row: stroka,
    already_written: povtor,
    imported: true,
    name: imya,
    not_written: [],
    shifted: null,
    notes: [],
    ...chastichno,
  }
  return HttpResponse.json(otvet)
}

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'cook@example.com', display_name: 'Повар', roles: ['cook'] }),
  ),
  http.get('/api/sync', () =>
    HttpResponse.json({ data_as_of: null, changed_at: null, stale: false, books: [] }),
  ),
  http.get('/api/cards/options', () =>
    HttpResponse.json({ categories: ['Сыры', 'Мясо'], suppliers: ['Метро'] }),
  ),
  // «Моцарелла» уже в таблице у другого поставщика — как в тесте дубля.
  http.get('/api/cards/name-check', () =>
    HttpResponse.json({
      cards: { exact: [{ name: 'Моцарелла', supplier: 'Молочный двор' }], similar: [] },
      hidden: { exact: [], similar: [] },
      reference: { exact: [], similar: [] },
    }),
  ),
  http.get('/api/cards/drafts/current', () => HttpResponse.json(chernovik)),
  http.post('/api/cards/drafts', () => {
    sozdano += 1
    const novyi = chernovikNa('supplier', {
      id: NOVYI,
      supplier: '',
      category: '',
      name: '',
      approval: null,
      photos: { label: false, package: false, before: false, after: false },
    })
    chernovik = novyi
    return HttpResponse.json(novyi, { status: 201 })
  }),
  http.patch('/api/cards/drafts/:id', async ({ request, params }) => {
    if (!chernovik || chernovik.id !== params.id) {
      return HttpResponse.json({ detail: 'Черновик не найден — обновите страницу' }, { status: 404 })
    }
    const pravka = (await request.json()) as DraftPatch
    pravki.push(pravka)
    const novyi = { ...chernovik, ...pravka } as Draft
    chernovik = { ...novyi, missing: nedostayot(novyi) }
    return HttpResponse.json(chernovik)
  }),
  http.put('/api/cards/drafts/:id/photos/:vid', async ({ request, params }) => {
    const foto = (await request.formData()).get('photo')
    zagruzki.push({
      vid: String(params.vid),
      foto: foto === null || typeof foto === 'string' ? String(foto) : await foto.text(),
      zashchita: request.headers.get('X-Kitchen-Csrf'),
    })
    const svoi = await otvetyZagruzki.shift()?.()
    if (svoi) return svoi
    chernovik = {
      ...chernovik!,
      photos: { ...chernovik!.photos, [String(params.vid)]: true },
      updated_at: vremyaPravki(),
    }
    return HttpResponse.json(chernovik)
  }),
  http.delete('/api/cards/drafts/:id/photos/:vid', async ({ request, params }) => {
    udaleniya.push({ vid: String(params.vid), zashchita: request.headers.get('X-Kitchen-Csrf') })
    const svoi = await otvetyUdaleniya.shift()?.()
    if (svoi) return svoi
    chernovik = {
      ...chernovik!,
      photos: { ...chernovik!.photos, [String(params.vid)]: false },
      updated_at: vremyaPravki(),
    }
    return HttpResponse.json(chernovik)
  }),
  http.post('/api/cards/drafts/:id/submit', async ({ request, params }) => {
    otpravki.push({ id: String(params.id), zashchita: request.headers.get('X-Kitchen-Csrf') })
    const otvet = otvetyOtpravki.shift()
    if (otvet === 'obryv') return HttpResponse.error()
    const svoi = await otvet?.()
    return svoi ?? zapisano()
  }),
)

let vernutFormData: () => void
let brauzer: ReturnType<typeof podmenitBrauzer>

beforeAll(async () => {
  vernutFormData = await podmenitFormData()
  server.listen({ onUnhandledRequest: 'error' })
})
beforeEach(() => {
  chernovik = null
  pravki = []
  zagruzki = []
  otpravki = []
  sozdano = 0
  otvetyZagruzki = []
  otvetyOtpravki = []
  otvetyUdaleniya = []
  udaleniya = []
  stroka = null
  pravokFoto = 0
  brauzer = podmenitBrauzer()
})
afterEach(() => {
  server.resetHandlers()
  brauzer.vernut()
  vi.useRealTimers()
})
afterAll(() => {
  vernutFormData()
  server.close()
})

/** Часы теста: идут сами, но их можно перевести вперёд — на срок отправки. */
function chasy() {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  return {
    polzovatel: userEvent.setup({ advanceTimers: vi.advanceTimersByTime }),
    vperyod: (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms)),
  }
}

/** Повар вернулся к черновику и нажал «Продолжить». */
async function prodolzhit(
  nachalo: Draft,
  polzovatel: Pick<ReturnType<typeof userEvent.setup>, 'click'> = userEvent,
) {
  chernovik = nachalo
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/cards']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await polzovatel.click(await screen.findByRole('button', { name: 'Продолжить' }))
  return { queries }
}

/** Ответ, который держит тест: отпускается, когда тесту нужно. */
function otlozhennyi() {
  let otpustit: () => void = () => {}
  const zhdat = new Promise<void>((gotovo) => {
    otpustit = gotovo
  })
  return { zhdat, otpustit }
}

function knopka(nazvanie: string): HTMLElement {
  return screen.getByRole('button', { name: nazvanie })
}

const ZAPISANO = `Записано в таблицу, строка ${STROKA}`

// ---------------------------------------------------------------------------
// Шаг 6. Согласован ли продукт
// ---------------------------------------------------------------------------

test('«Отбракован» — сразу к итогу, «Назад» оттуда — к вопросу; «Да» — к фото продукта', async () => {
  await prodolzhit(chernovikNa('approval', { approval: null }))
  expect(screen.getByText('Шаг 6 из 9')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Согласован ли продукт' })).toBeInTheDocument()

  await userEvent.click(knopka('Отбракован'))

  // Фото продукта и описание отбракованному не нужны.
  expect(await screen.findByText('Шаг 9 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ approval: 'Отбракован', step: 'summary' }])

  await userEvent.click(knopka('Назад'))

  expect(await screen.findByText('Шаг 6 из 9')).toBeInTheDocument()
  expect(pravki[1]).toEqual({ step: 'approval' })
  expect(knopka('Отбракован')).toHaveAttribute('aria-pressed', 'true')
  expect(knopka('Да')).toHaveAttribute('aria-pressed', 'false')

  await userEvent.click(knopka('Да'))

  expect(await screen.findByText('Шаг 7 из 9')).toBeInTheDocument()
  expect(pravki[2]).toEqual({ approval: 'Да', step: 'photos' })
})

// ---------------------------------------------------------------------------
// Шаг 7. Три фото продукта
// ---------------------------------------------------------------------------

test('три фото продукта необязательны — «Пропустить» ведёт к описанию', async () => {
  await prodolzhit(chernovikNa('photos'))
  expect(screen.getByText('Шаг 7 из 9')).toBeInTheDocument()

  for (const slot of ['В упаковке', 'До обработки', 'После обработки']) {
    const gruppa = screen.getByRole('group', { name: slot })
    expect(within(gruppa).getByLabelText('Сфотографировать')).toBeEnabled()
    expect(within(gruppa).getByLabelText('Из галереи')).toBeEnabled()
  }
  expect(screen.queryByRole('button', { name: 'Далее' })).not.toBeInTheDocument()

  await userEvent.click(knopka('Пропустить'))

  expect(await screen.findByText('Шаг 8 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ step: 'description' }])
  expect(zagruzki).toEqual([])
})

test('фото продукта уменьшается и ложится в свой слот; пока грузится — «Назад» и «Пропустить» ждут', async () => {
  const zagruzka = otlozhennyi()
  otvetyZagruzki = [
    async () => {
      await zagruzka.zhdat
      return undefined
    },
  ]
  await prodolzhit(chernovikNa('photos'))
  const doObrabotki = screen.getByRole('group', { name: 'До обработки' })

  await userEvent.upload(within(doObrabotki).getByLabelText('Сфотографировать'), snimok(4032, 3024))

  expect(await within(doObrabotki).findByText('Загружаем фото…')).toBeInTheDocument()
  // Ушедший шаг не дождался бы загрузки.
  expect(knopka('Назад')).toBeDisabled()
  expect(knopka('Пропустить')).toBeDisabled()
  // Другие слоты не заняты.
  expect(
    within(screen.getByRole('group', { name: 'После обработки' })).getByLabelText('Сфотографировать'),
  ).toBeEnabled()

  zagruzka.otpustit()

  // Фото есть — главная кнопка уже не «Пропустить», а «Далее».
  await waitFor(() => expect(knopka('Далее')).toBeEnabled())
  expect(zagruzki).toEqual([{ vid: 'before', foto: 'jpeg 1600x1200', zashchita: '1' }])
  expect(within(doObrabotki).getByRole('img', { name: 'Фото до обработки' })).toHaveAttribute(
    'src',
    expect.stringMatching(/^data:image\/jpeg;base64,/),
  )
  expect(within(doObrabotki).getByLabelText('Переснять')).toBeInTheDocument()
  expect(
    within(screen.getByRole('group', { name: 'В упаковке' })).getByLabelText('Сфотографировать'),
  ).toBeInTheDocument()

  await userEvent.click(knopka('Далее'))

  expect(await screen.findByText('Шаг 8 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ step: 'description' }])
})

test('уже загруженное фото продукта показывается с сервера', async () => {
  await prodolzhit(
    chernovikNa('photos', { photos: { label: true, package: true, before: false, after: false } }),
  )

  const foto = within(screen.getByRole('group', { name: 'В упаковке' })).getByRole('img', {
    name: 'Фото в упаковке',
  })
  expect(foto.getAttribute('src')).toMatch(new RegExp(`^/api/cards/drafts/${ID}/photos/package\\?v=`))
  expect(knopka('Далее')).toBeEnabled()
})

// ---------------------------------------------------------------------------
// Шаг 8. Описание
// ---------------------------------------------------------------------------

test('описание уходит как набрано — с переносами строк', async () => {
  await prodolzhit(chernovikNa('description'))
  expect(screen.getByText('Шаг 8 из 9')).toBeInTheDocument()
  // Описание необязательно — «Далее» доступна и с пустым.
  expect(knopka('Далее')).toBeEnabled()

  await userEvent.type(screen.getByRole('textbox', { name: 'Описание' }), 'Для пиццы{Enter}тянется')
  await userEvent.click(knopka('Далее'))

  expect(await screen.findByText('Шаг 9 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ description: 'Для пиццы\nтянется', step: 'summary' }])
})

// ---------------------------------------------------------------------------
// Шаг 9. Итог и отправка
// ---------------------------------------------------------------------------

test('в итоге видно, чего не хватает: «Отправить» недоступна, касание ведёт к шагу', async () => {
  await prodolzhit(chernovikNa('summary', { approval: null }))
  expect(screen.getByText('Шаг 9 из 9')).toBeInTheDocument()

  expect(screen.getByText(/Чтобы отправить карточку, заполните/)).toBeInTheDocument()
  expect(knopka('Отправить в таблицу')).toBeDisabled()

  await userEvent.click(knopka('Согласован ли продукт'))

  expect(await screen.findByText('Шаг 6 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ step: 'approval' }])
  expect(otpravki).toEqual([])
})

test('успех — «Записано в таблицу, строка N», «Добавить ещё» начинает новую карточку', async () => {
  const { queries } = await prodolzhit(chernovikNa('summary', { description: 'Для пиццы' }))
  // Справочник, сверка и подсказки уже в кэше — после отправки они устарели.
  queries.setQueryData(['reconciliation'], { total: 0, linked: 0, needs_human: 0, rows: [] })
  queries.setQueryData(['ingredients', { limit: 500 }], [])
  queries.setQueryData(['kartochki', 'varianty'], { categories: ['Сыры'], suppliers: [] })

  // Что уйдёт в таблицу — видно до отправки.
  const svodka = screen.getByRole('list', { name: 'Карточка' })
  expect(within(svodka).getByText('Метро')).toBeInTheDocument()
  expect(within(svodka).getByText('Сыры')).toBeInTheDocument()
  expect(within(svodka).getByText('Моцарелла')).toBeInTheDocument()
  expect(within(svodka).getByText('Да')).toBeInTheDocument()
  expect(within(svodka).getByText('Для пиццы')).toBeInTheDocument()

  await userEvent.click(knopka('Отправить в таблицу'))

  const zagolovok = await screen.findByRole('heading', { name: ZAPISANO })
  expect(zagolovok).toHaveFocus()
  expect(screen.getByText('Моцарелла')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  // Один запрос — с защитой от подделки.
  expect(otpravki).toEqual([{ id: ID, zashchita: '1' }])
  expect(queries.getQueryState(['reconciliation'])?.isInvalidated).toBe(true)
  expect(queries.getQueryState(['ingredients', { limit: 500 }])?.isInvalidated).toBe(true)
  // Новый поставщик — в подсказках следующей карточки.
  expect(queries.getQueryState(['kartochki', 'varianty'])?.isInvalidated).toBe(true)
  // Черновик перечитан: он отправлен, на сервере его больше нет.
  await waitFor(() => expect(queries.getQueryData(KLYUCH_CHERNOVIKA)).toBeNull())
  expect(screen.getByRole('heading', { name: ZAPISANO })).toBeInTheDocument()

  await userEvent.click(knopka('Добавить ещё'))

  expect(await screen.findByText('Шаг 1 из 9')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Поставщик' })).toHaveValue('')
  // «Добавить ещё» исчезла вместе с итогом — фокус на заголовке нового шага.
  expect(screen.getByRole('heading', { name: 'Поставщик' })).toHaveFocus()
  expect(sozdano).toBe(1)
})

test('дубль в таблице (409) — «Изменить название» ведёт на шаг названия', async () => {
  otvetyOtpravki = [
    () =>
      HttpResponse.json(
        { detail: '«Моцарелла» уже есть в таблице — строка 3.', row: 3 },
        { status: 409 },
      ),
  ]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('alert')).toHaveTextContent(
    '«Моцарелла» уже есть в таблице — строка 3.',
  )
  // Повтор дал бы тот же отказ — главное и единственное действие одно.
  expect(knopka('Изменить название')).toHaveClass('kartochka-glavnaya')
  expect(screen.queryByRole('button', { name: /^Отправить/ })).not.toBeInTheDocument()
  await userEvent.click(knopka('Изменить название'))

  expect(await screen.findByText('Шаг 3 из 9')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Название' })).toHaveValue('Моцарелла')
  // На шаге названия тот же дубль виден сразу — повар знает, что менять.
  expect(await screen.findByText(/вторую карточку с этим названием не запишут/)).toBeInTheDocument()
  expect(pravki).toEqual([{ step: 'name' }])
  expect(otpravki).toHaveLength(1)
})

test('Google не принял запись (502) — «черновик сохранён», повтор той же отправкой', async () => {
  otvetyOtpravki = [() => HttpResponse.json({ detail: GOOGLE_NE_PRINYAL }, { status: 502 })]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('alert')).toHaveTextContent(GOOGLE_NE_PRINYAL)
  await userEvent.click(knopka('Отправить ещё раз'))

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  expect(otpravki.map((o) => o.id)).toEqual([ID, ID])
})

test('сервер не ответил (502 nginx, не JSON) — свой текст: черновик сохранён', async () => {
  otvetyOtpravki = [
    () =>
      new HttpResponse('<html>502 Bad Gateway</html>', {
        status: 502,
        headers: { 'Content-Type': 'text/html' },
      }),
  ]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  const otkaz = await screen.findByRole('alert')
  expect(otkaz).toHaveTextContent(/Черновик сохранён/)
  expect(otkaz).not.toHaveTextContent('Не удалось получить данные')
  expect(knopka('Отправить ещё раз')).toBeEnabled()
})

test.each([
  ['колонки сдвинулись', SDVIG, false],
  ['таблица занята', ZANYATA, true],
  ['не настроено', NE_NASTROENA, false],
])('503 %s — текст сервера, отправка сама не повторяется', async (_, tekst, glavnaya) => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [() => HttpResponse.json({ detail: tekst }, { status: 503 })]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('alert')).toHaveTextContent(tekst)
  await vperyod(120_000)
  expect(otpravki).toHaveLength(1)
  // Повар повторит позже сам — черновик на месте. Где нужен шеф или
  // администратор, повтор — не главное действие: сам по себе он не поможет.
  const povtor = knopka('Отправить ещё раз')
  expect(povtor).toBeEnabled()
  if (glavnaya) expect(povtor).toHaveClass('kartochka-glavnaya')
  else expect(povtor).not.toHaveClass('kartochka-glavnaya')
  expect(screen.getByText('Шаг 9 из 9')).toBeInTheDocument()
})

test.each([
  ['отправка уже идёт', 'Карточка отправляется или отправка прервалась — попробуйте через 3 минуты'],
  ['идёт распознавание', 'Дождитесь окончания распознавания'],
])('409 — %s: текст, сами не повторяем', async (_, tekst) => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [() => HttpResponse.json({ detail: tekst }, { status: 409 })]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('alert')).toHaveTextContent(tekst)
  expect(screen.queryByRole('button', { name: 'Изменить название' })).not.toBeInTheDocument()
  await vperyod(120_000)
  expect(otpravki).toHaveLength(1)
})

test('не хватает полей (422) — список недостающего и переход к нему', async () => {
  otvetyOtpravki = [
    () =>
      HttpResponse.json(
        {
          detail: 'Чтобы отправить карточку, заполните: Название, Фото этикетки',
          missing: ['Название', 'Фото этикетки'],
        },
        { status: 422 },
      ),
  ]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Чтобы отправить карточку, заполните: Название, Фото этикетки',
  )
  expect(knopka('Название')).toBeEnabled()
  await userEvent.click(knopka('Фото этикетки'))

  expect(await screen.findByText('Шаг 4 из 9')).toBeInTheDocument()
  expect(pravki).toEqual([{ step: 'label' }])
})

test('двойное касание «Отправить» — один запрос; пока идёт отправка, кнопки ждут', async () => {
  const otvet = otlozhennyi()
  otvetyOtpravki = [
    async () => {
      await otvet.zhdat
      return undefined
    },
  ]
  await prodolzhit(chernovikNa('summary'))

  // Два касания подряд, быстрее, чем экран успел перерисоваться: второе
  // застаёт кнопку ещё доступной. Одним act — иначе перерисовка (она
  // синхронна после касания) успела бы сделать кнопку серой, и тест не
  // дошёл бы до защиты от второй отправки.
  const otpravit = knopka('Отправить в таблицу')
  act(() => {
    otpravit.click()
    otpravit.click()
  })

  expect(await screen.findByText(/Отправляем карточку в таблицу/)).toBeInTheDocument()
  expect(knopka('Отправить в таблицу')).toBeDisabled()
  expect(knopka('Назад')).toBeDisabled()
  fireEvent.click(knopka('Отправить в таблицу'))

  otvet.otpustit()

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  expect(otpravki).toHaveLength(1)
})

test('обрыв связи и срок 60 с — та же отправка повторяется сама', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [
    // Запрос не дошёл до сервера: связь оборвалась сразу.
    'obryv',
    async () => {
      // Дошёл, сервер записал строку, а ответ не дошёл: телефон ушёл из сети.
      zapisano()
      await delay('infinite')
      return undefined
    },
    // Третья попытка — повтор уже записанной: та же строка, второй нет.
    () => zapisano({ already_written: true }),
  ]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))

  expect(await screen.findByText(/отправляем ещё раз \(попытка 2 из 3\)/)).toBeInTheDocument()
  expect(screen.getByText(/второй строки в таблице не будет/)).toBeInTheDocument()
  await vperyod(2_000)
  await waitFor(() => expect(otpravki).toHaveLength(2))
  await vperyod(59_000)
  expect(otpravki).toHaveLength(2)

  await vperyod(1_000)
  expect(await screen.findByText(/попытка 3 из 3/)).toBeInTheDocument()
  await vperyod(2_000)

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  // Каждый раз — тот же черновик: сервер узнаёт повтор и второй строки не пишет.
  expect(otpravki.map((o) => o.id)).toEqual([ID, ID, ID])
})

test('ответ так и не дошёл — понятный текст и «Отправить ещё раз»', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = ['obryv', 'obryv', 'obryv']
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))
  await screen.findByText(/попытка 2 из 3/)
  await vperyod(2_000)
  await screen.findByText(/попытка 3 из 3/)
  await vperyod(2_000)

  const otkaz = await screen.findByRole('alert')
  expect(otkaz).toHaveTextContent(/Черновик сохранён/)
  expect(otkaz).toHaveTextContent(/второй строки в таблице не будет/)
  expect(otpravki).toHaveLength(3)

  await polzovatel.click(knopka('Отправить ещё раз'))

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
})

test('карточка записана, но на сайт пока не перенесена — успех без тревоги', async () => {
  otvetyOtpravki = [() => zapisano({ imported: false, notes: [NE_PERENESENA] })]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  const zametka = screen.getByText(NE_PERENESENA)
  expect(zametka.closest('.kartochka-zamechanie')).toBeNull()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('оговорки сервера для шефа — повару как есть, заметно', async () => {
  const pravkiNePopali =
    'Карточка уже в строке 128 в первой версии; правки «Жиры» не попали — скажите шефу'
  const tablitsuMenyali =
    'При первой попытке таблицу меняли — покажите шефу строку 128 (запись журнала №41)'
  otvetyOtpravki = [
    () =>
      zapisano({
        already_written: true,
        not_written: ['Жиры'],
        shifted: { row: 128, journal_id: 41 },
        notes: [pravkiNePopali, tablitsuMenyali],
      }),
  ]
  await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  expect(screen.getByText(pravkiNePopali).closest('.kartochka-zamechanie')).not.toBeNull()
  expect(screen.getByText(tablitsuMenyali).closest('.kartochka-zamechanie')).not.toBeNull()
})

// ---------------------------------------------------------------------------
// Круг правок 1: своя отправка ещё пишет, оборванное тело, удаление фото,
// опоздавший ответ, «Назад» и отказ у описания
// ---------------------------------------------------------------------------

const OTPRAVLYAETSYA_17 =
  'Карточка отправляется или отправка прервалась — попробуйте через 17 минут'
const ZAPISYVAETSYA = 'Карточка ещё записывается — ждём ответа таблицы…'

test('обрыв, а повтор застал свою же отправку (409) — ждём таблицу и показываем строку', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [
    // Связь оборвалась, пока сервер пишет строку (это 3–15 с).
    'obryv',
    // Повтор через 2 с застаёт её ещё идущей: отметка «отправляется» свежая.
    () => HttpResponse.json({ detail: OTPRAVLYAETSYA_17 }, { status: 409 }),
    // Первая закончилась — повтор отвечает её строкой.
    () => zapisano({ already_written: true }),
  ]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))
  await screen.findByText(/попытка 2 из 3/)
  await vperyod(2_000)

  // Не «ждите 17 минут»: это наша же отправка, она вот-вот закончится.
  expect(await screen.findByText(ZAPISYVAETSYA)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByText(/17 минут/)).not.toBeInTheDocument()
  expect(knopka('Отправить в таблицу')).toBeDisabled()

  await vperyod(5_000)

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  expect(otpravki.map((o) => o.id)).toEqual([ID, ID, ID])
})

test('своя отправка не кончилась и за полминуты — текст сервера', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [
    'obryv',
    ...Array.from(
      { length: 7 },
      () => () => HttpResponse.json({ detail: OTPRAVLYAETSYA_17 }, { status: 409 }),
    ),
  ]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))
  await screen.findByText(/попытка 2 из 3/)
  await vperyod(2_000)
  await screen.findByText(ZAPISYVAETSYA)
  for (let raz = 0; raz < 6; raz += 1) await vperyod(5_000)

  expect(await screen.findByRole('alert')).toHaveTextContent(OTPRAVLYAETSYA_17)
  // Обрыв, повтор и шесть ожиданий по 5 с — и больше сами не повторяем.
  expect(otpravki).toHaveLength(8)
  await vperyod(60_000)
  expect(otpravki).toHaveLength(8)
  expect(knopka('Отправить ещё раз')).toBeEnabled()
})

test('ответ отправки оборвался посреди тела — это обрыв: повтор показывает строку', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyOtpravki = [
    () => {
      // Строка легла, а ответ дошёл не целиком.
      zapisano()
      return new HttpResponse('{"row": 12', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    },
    () => zapisano({ already_written: true }),
  ]
  await prodolzhit(chernovikNa('summary'), polzovatel)

  await polzovatel.click(knopka('Отправить в таблицу'))
  expect(await screen.findByText(/попытка 2 из 3/)).toBeInTheDocument()
  await vperyod(2_000)

  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
  expect(screen.queryByText('Не удалось получить данные')).not.toBeInTheDocument()
  expect(otpravki).toHaveLength(2)
})

test('пока идёт отправка, перечитанное «черновика нет» не уводит с итога', async () => {
  const otvet = otlozhennyi()
  otvetyOtpravki = [
    async () => {
      // Строка легла, черновик отмечен отправленным — а ответ ещё в пути.
      chernovik = null
      await otvet.zhdat
      return undefined
    },
  ]
  const { queries } = await prodolzhit(chernovikNa('summary'))

  await userEvent.click(knopka('Отправить в таблицу'))
  await screen.findByText(/Отправляем карточку в таблицу/)
  await waitFor(() => expect(chernovik).toBeNull())
  // Повар вернулся во вкладку — черновик перечитывается и приходит «нет».
  await queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })
  await waitFor(() => expect(queries.isFetching({ queryKey: KLYUCH_CHERNOVIKA })).toBe(0))

  expect(screen.getByText('Шаг 9 из 9')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Начать' })).not.toBeInTheDocument()

  otvet.otpustit()
  expect(await screen.findByRole('heading', { name: ZAPISANO })).toBeInTheDocument()
})

test('фото продукта можно убрать — после вопроса; пока убирается, шаг ждёт', async () => {
  const udalenie = otlozhennyi()
  otvetyUdaleniya = [
    async () => {
      await udalenie.zhdat
      return undefined
    },
  ]
  await prodolzhit(chernovikNa('photos'))
  const doObrabotki = screen.getByRole('group', { name: 'До обработки' })
  await userEvent.upload(within(doObrabotki).getByLabelText('Сфотографировать'), snimok(4032, 3024))
  await waitFor(() => expect(knopka('Далее')).toBeEnabled())

  // Сначала вопрос: «до обработки» после обработки уже не переснять.
  await userEvent.click(within(doObrabotki).getByRole('button', { name: 'Убрать' }))
  expect(within(doObrabotki).getByText(/Убрать фото/)).toBeInTheDocument()
  expect(within(doObrabotki).getByRole('button', { name: 'Нет, оставить' })).toHaveFocus()
  await userEvent.click(within(doObrabotki).getByRole('button', { name: 'Нет, оставить' }))
  expect(udaleniya).toEqual([])
  expect(within(doObrabotki).getByRole('button', { name: 'Убрать' })).toHaveFocus()

  await userEvent.click(within(doObrabotki).getByRole('button', { name: 'Убрать' }))
  await userEvent.click(within(doObrabotki).getByRole('button', { name: 'Да, убрать' }))

  await waitFor(() => expect(udaleniya).toEqual([{ vid: 'before', zashchita: '1' }]))
  expect(knopka('Назад')).toBeDisabled()
  expect(knopka('Далее')).toBeDisabled()

  udalenie.otpustit()

  // Фото нет — ни превью этой загрузки, ни кнопки «Убрать»; снова «Пропустить».
  await waitFor(() => expect(within(doObrabotki).queryByRole('img')).not.toBeInTheDocument())
  expect(within(doObrabotki).getByLabelText('Сфотографировать')).toBeEnabled()
  expect(within(doObrabotki).queryByRole('button', { name: 'Убрать' })).not.toBeInTheDocument()
  expect(knopka('Пропустить')).toBeEnabled()
})

test('убрать фото во время отправки (409) — текст, фото на месте', async () => {
  otvetyUdaleniya = [
    () => HttpResponse.json({ detail: 'Карточка отправляется — подождите' }, { status: 409 }),
  ]
  await prodolzhit(
    chernovikNa('photos', { photos: { label: true, package: true, before: false, after: false } }),
  )
  const vUpakovke = screen.getByRole('group', { name: 'В упаковке' })

  await userEvent.click(within(vUpakovke).getByRole('button', { name: 'Убрать' }))
  await userEvent.click(within(vUpakovke).getByRole('button', { name: 'Да, убрать' }))

  expect(await within(vUpakovke).findByRole('alert')).toHaveTextContent(
    'Карточка отправляется — подождите',
  )
  expect(within(vUpakovke).getByRole('img', { name: 'Фото в упаковке' })).toBeInTheDocument()
  expect(udaleniya).toHaveLength(1)
})

test('пока фото слота загружается, «Убрать» недоступна', async () => {
  const zagruzka = otlozhennyi()
  otvetyZagruzki = [
    async () => {
      await zagruzka.zhdat
      return undefined
    },
  ]
  await prodolzhit(
    chernovikNa('photos', { photos: { label: true, package: true, before: false, after: false } }),
  )
  const vUpakovke = screen.getByRole('group', { name: 'В упаковке' })

  await userEvent.upload(within(vUpakovke).getByLabelText('Переснять'), snimok(4032, 3024))

  expect(await within(vUpakovke).findByText('Загружаем фото…')).toBeInTheDocument()
  expect(within(vUpakovke).getByRole('button', { name: 'Убрать' })).toBeDisabled()
  zagruzka.otpustit()
  await waitFor(() =>
    expect(within(vUpakovke).getByRole('button', { name: 'Убрать' })).toBeEnabled(),
  )
})

test('опоздавший ответ загрузки (старше в кэше) не откатывает соседнее фото', async () => {
  const pervaya = otlozhennyi()
  let zagruzok = 0
  server.use(
    http.put('/api/cards/drafts/:id/photos/:vid', async ({ params }) => {
      zagruzok += 1
      const nomer = zagruzok
      chernovik = {
        ...chernovik!,
        photos: { ...chernovik!.photos, [String(params.vid)]: true },
        updated_at: vremyaPravki(),
      }
      // Ответ — каким черновик был после этой загрузки; первый держится.
      const otvet = chernovik
      if (nomer === 1) await pervaya.zhdat
      return HttpResponse.json(otvet)
    }),
  )
  await prodolzhit(chernovikNa('photos'))
  const vUpakovke = screen.getByRole('group', { name: 'В упаковке' })
  const doObrabotki = screen.getByRole('group', { name: 'До обработки' })

  await userEvent.upload(within(vUpakovke).getByLabelText('Сфотографировать'), snimok(4032, 3024))
  await waitFor(() => expect(zagruzok).toBe(1))
  await userEvent.upload(within(doObrabotki).getByLabelText('Сфотографировать'), snimok(4032, 3024))
  // Вторая ответила первой — в ней уже оба фото.
  expect(await within(doObrabotki).findByRole('button', { name: 'Убрать' })).toBeInTheDocument()

  pervaya.otpustit()
  await waitFor(() =>
    expect(within(vUpakovke).queryByText('Загружаем фото…')).not.toBeInTheDocument(),
  )

  // Опоздавший ответ первой (без второго фото) лёг бы поверх и «потерял» его.
  expect(within(doObrabotki).getByRole('button', { name: 'Убрать' })).toBeInTheDocument()
  expect(within(vUpakovke).getByRole('button', { name: 'Убрать' })).toBeInTheDocument()
})

test('описание: отказ у поля (422) и «Назад» уносит набранное', async () => {
  let otkazat = true
  server.use(
    http.patch('/api/cards/drafts/:id', async ({ request }) => {
      const pravka = (await request.json()) as DraftPatch
      pravki.push(pravka)
      if (otkazat) {
        return HttpResponse.json(
          { detail: 'Описание: не больше 2000 знаков', field: 'description' },
          { status: 422 },
        )
      }
      chernovik = { ...chernovik!, ...pravka } as Draft
      return HttpResponse.json(chernovik)
    }),
  )
  await prodolzhit(chernovikNa('description'))
  const pole = screen.getByRole('textbox', { name: 'Описание' })

  await userEvent.type(pole, 'Тянется')
  await userEvent.click(knopka('Далее'))

  expect(await screen.findByText('Описание: не больше 2000 знаков')).toBeInTheDocument()
  expect(pole).toHaveAttribute('aria-invalid', 'true')
  expect(pole).toHaveAccessibleDescription(/Описание: не больше 2000 знаков/)
  expect(pole).toHaveFocus()

  otkazat = false
  await userEvent.click(knopka('Назад'))

  expect(await screen.findByText('Шаг 7 из 9')).toBeInTheDocument()
  expect(pravki.at(-1)).toEqual({ description: 'Тянется', step: 'photos' })
})
