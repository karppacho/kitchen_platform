import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import type { Draft, DraftPatch, Shag } from '../src/api/types'
import { App } from '../src/App'
import { heic, podmenitBrauzer, podmenitFormData, snimok } from './podmenaFoto'

const ID = '0b6f3f5e-6a51-4f43-9a52-6f1d1c0e7a01'

/** Черновик на нужном шаге: поставщик, категория и название уже есть. */
function chernovikNa(step: Shag, chastichno: Partial<Draft> = {}): Draft {
  return {
    id: ID,
    status: 'active',
    step,
    supplier: 'Метро',
    category: 'Сыры',
    name: 'Моцарелла',
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
    missing: ['Фото этикетки', 'Согласован ли продукт'],
    created_at: '2026-10-01T08:00:00Z',
    updated_at: '2026-10-01T08:00:00Z',
    ...chastichno,
  }
}

const S_ETIKETKOI = { label: true, package: false, before: false, after: false }

/** Одиннадцать полей, какими их кладёт в черновик распознавание. КБЖУ —
 *  строками, как во всём API. */
const PROCHITANO = {
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
} satisfies Partial<Draft>

const ZAMECHANIE =
  'Пищевая ценность на этикетке — на 100 мл, а колонка в таблице — на 100 г; числа перенесены как есть, проверьте.'

const NE_OTVECHAET =
  'polza.ai не отвечает. Попробуйте ещё раз через минуту. Пока заполните поля вручную.'
const LIMIT =
  'Вы сегодня распознали уже 40 этикеток — это предел на день. Заполните поля вручную.'
const NE_NASTROENO = 'Распознавание не настроено — заполните поля вручную'
const UZHE_IDYOT = 'Этикетка уже распознаётся — подождите'

// Что «сервер» знает и что к нему приходило — заново на каждый тест.
let chernovik: Draft | null
let pravki: DraftPatch[]
let zagruzki: { vid: string; foto: string; zashchita: string | null }[]
let raspoznavaniya: (string | null)[]
let oprosov: number
/** Ответы загрузки по одной на попытку; кончились — фото принято. */
let otvetyZagruzki: ('obryv' | (() => Response))[]
let raspoznat: () => Response | Promise<Response>
let poOprosu: ((nomer: number) => void) | null

/** Распознавание закончилось: поля, замечания, статус — в черновике. */
function raspoznanoNaServere() {
  chernovik = {
    ...chernovik!,
    ...PROCHITANO,
    recognition_status: 'done',
    recognition_error: null,
    warnings: [ZAMECHANIE],
    updated_at: '2026-10-01T08:05:00Z',
  }
}

function raspoznano(): Response {
  raspoznanoNaServere()
  return HttpResponse.json(chernovik)
}

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'cook@example.com', display_name: 'Повар', roles: ['cook'] }),
  ),
  http.get('/api/sync', () =>
    HttpResponse.json({ data_as_of: null, changed_at: null, stale: false, books: [] }),
  ),
  http.get('/api/cards/drafts/current', () => {
    oprosov += 1
    poOprosu?.(oprosov)
    return HttpResponse.json(chernovik)
  }),
  http.patch('/api/cards/drafts/:id', async ({ request }) => {
    const pravka = (await request.json()) as DraftPatch
    pravki.push(pravka)
    if (pravka.protein === 'abc') {
      return HttpResponse.json({ detail: 'Белки: «abc» — не число', field: 'protein' }, { status: 422 })
    }
    chernovik = { ...chernovik!, ...pravka } as Draft
    return HttpResponse.json(chernovik)
  }),
  http.put('/api/cards/drafts/:id/photos/:vid', async ({ request, params }) => {
    const foto = (await request.formData()).get('photo')
    zagruzki.push({
      vid: String(params.vid),
      foto: foto === null || typeof foto === 'string' ? String(foto) : await foto.text(),
      zashchita: request.headers.get('X-Kitchen-Csrf'),
    })
    const otvet = otvetyZagruzki.shift()
    if (otvet === 'obryv') return HttpResponse.error()
    if (otvet) return otvet()
    // Новая этикетка сбрасывает распознавание прежней; поля остаются.
    chernovik = {
      ...chernovik!,
      photos: { ...chernovik!.photos, [String(params.vid)]: true },
      recognition_status: null,
      recognition_error: null,
      warnings: [],
      updated_at: '2026-10-01T08:01:00Z',
    }
    return HttpResponse.json(chernovik)
  }),
  http.post('/api/cards/recognize/:id', ({ request }) => {
    raspoznavaniya.push(request.headers.get('X-Kitchen-Csrf'))
    return raspoznat()
  }),
)

let vernutFormData: () => void

beforeAll(async () => {
  vernutFormData = await podmenitFormData()
  server.listen({ onUnhandledRequest: 'error' })
})

let brauzer: ReturnType<typeof podmenitBrauzer>

beforeEach(() => {
  chernovik = null
  pravki = []
  zagruzki = []
  raspoznavaniya = []
  oprosov = 0
  otvetyZagruzki = []
  raspoznat = raspoznano
  poOprosu = null
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

/** Часы теста: идут сами, но их можно перевести вперёд — на паузу перед
 *  повтором загрузки, на опрос черновика раз в 3 с. */
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
  polzovatel: ReturnType<typeof userEvent.setup> = userEvent.setup(),
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
}

function pole(nazvanie: string): HTMLElement {
  return screen.getByRole('textbox', { name: nazvanie })
}

/** Ответ, который держит тест: отпускается, когда тесту нужно. */
function otlozhennyi() {
  let otpustit: () => void = () => {}
  const zhdat = new Promise<void>((gotovo) => {
    otpustit = gotovo
  })
  return { zhdat, otpustit }
}

// ---------------------------------------------------------------------------
// Фото этикетки
// ---------------------------------------------------------------------------

test('фото уменьшается, загружается, распознаётся — форма заполнена, замечания видны', async () => {
  const model = otlozhennyi()
  raspoznat = async () => {
    await model.zhdat
    return raspoznano()
  }
  await prodolzhit(chernovikNa('label'))

  expect(screen.getByText('Шаг 4 из 9')).toBeInTheDocument()
  // Без фото этикетки дальше нельзя — и сказано почему.
  expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled()
  expect(screen.getByText(/станет доступна, когда фото загрузится/)).toBeInTheDocument()

  await userEvent.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))

  expect(await screen.findByText('Шаг 5 из 9')).toBeInTheDocument()
  expect(await screen.findByText(/Распознаём этикетку/)).toBeInTheDocument()
  // Пока модель читает, полей нет: её итог лёг бы поверх набранного.
  expect(screen.queryByRole('textbox', { name: 'Белки' })).not.toBeInTheDocument()

  model.otpustit()
  expect(await screen.findByRole('textbox', { name: 'Белки' })).toHaveValue('18')
  expect(pole('Название по этикетке')).toHaveValue('Сыр моцарелла 45%')
  expect(pole('Изготовитель')).toHaveValue('Сыроварня «Пример»')
  expect(pole('Состав')).toHaveValue('молоко нормализованное, соль, закваска')
  expect(pole('Жиры')).toHaveValue('22.5')
  expect(pole('Углеводы')).toHaveValue('1.25')
  expect(pole('Ккал')).toHaveValue('280')
  expect(pole('Срок в закрытой упаковке')).toHaveValue('45 суток')
  expect(pole('Срок после нарезки / фасовки')).toHaveValue('72 часа')
  expect(pole('Срок после дефростации')).toHaveValue('')
  expect(pole('Условия дефростации')).toHaveValue('')
  expect(screen.getByText(ZAMECHANIE)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()

  // На сервер ушло уменьшенное фото, с защитой от подделки; распознавание
  // запущено само, один раз.
  expect(zagruzki).toEqual([{ vid: 'label', foto: 'jpeg 1600x1200', zashchita: '1' }])
  expect(raspoznavaniya).toEqual(['1'])
  expect(pravki).toEqual([{ step: 'review' }])

  // Повар поправил углеводы — число уходит строкой, как написано.
  await userEvent.clear(pole('Углеводы'))
  await userEvent.type(pole('Углеводы'), '1,3')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  expect(await screen.findByText('Шаг 6 из 9')).toBeInTheDocument()
  expect(pravki[1]).toEqual({ ...PROCHITANO, carbs: '1,3', step: 'approval' })
})

test('обрыв связи при загрузке — повтор тем же уменьшенным фото', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyZagruzki = ['obryv']
  await prodolzhit(chernovikNa('label'), polzovatel)

  await polzovatel.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))

  // Повар видит, что связь прервалась и загрузка не брошена.
  expect(await screen.findByText(/Связь прервалась — пробуем ещё раз/)).toBeInTheDocument()
  await vperyod(2000)

  expect(await screen.findByText('Шаг 5 из 9')).toBeInTheDocument()
  expect(zagruzki).toEqual([
    { vid: 'label', foto: 'jpeg 1600x1200', zashchita: '1' },
    { vid: 'label', foto: 'jpeg 1600x1200', zashchita: '1' },
  ])
})

test('загрузка оборвалась три раза — понятный текст и «Загрузить ещё раз» тем же фото', async () => {
  const { polzovatel, vperyod } = chasy()
  otvetyZagruzki = ['obryv', 'obryv', 'obryv']
  await prodolzhit(chernovikNa('label'), polzovatel)

  await polzovatel.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))
  await screen.findByText(/пробуем ещё раз \(попытка 2 из 3\)/)
  await vperyod(2000)
  await screen.findByText(/пробуем ещё раз \(попытка 3 из 3\)/)
  await vperyod(2000)

  expect(await screen.findByRole('alert')).toHaveTextContent(/Фото не загрузилось/)
  expect(zagruzki).toHaveLength(3)
  // Два автоповтора — и всё: дальше решает повар.
  await vperyod(10_000)
  expect(zagruzki).toHaveLength(3)
  expect(screen.getByText('Шаг 4 из 9')).toBeInTheDocument()

  // Фото с камеры не лежит в галерее — второй раз его не выбрать. Его
  // загружают ещё раз то же.
  await polzovatel.click(screen.getByRole('button', { name: 'Загрузить ещё раз' }))

  expect(await screen.findByText('Шаг 5 из 9')).toBeInTheDocument()
  expect(zagruzki.map((z) => z.foto)).toEqual(Array(4).fill('jpeg 1600x1200'))
})

test('фото не открылось — текст про формат, на сервер ничего не ушло', async () => {
  await prodolzhit(chernovikNa('label'))

  await userEvent.upload(screen.getByLabelText('Выбрать из галереи'), heic())

  const otkaz = await screen.findByRole('alert')
  expect(otkaz).toHaveTextContent(/HEIC/)
  expect(otkaz).toHaveTextContent(/«Наиболее совместимый»/)
  expect(zagruzki).toEqual([])
  expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Распознавание
// ---------------------------------------------------------------------------

test('распознавание не удалось (502) — пустая форма и «Распознать ещё раз»', async () => {
  raspoznat = () => {
    chernovik = { ...chernovik!, recognition_status: 'failed', recognition_error: NE_OTVECHAET }
    return HttpResponse.json({ detail: NE_OTVECHAET }, { status: 502 })
  }
  await prodolzhit(chernovikNa('label'))

  await userEvent.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))

  expect(await screen.findByRole('alert')).toHaveTextContent(NE_OTVECHAET)
  // Один и тот же текст — один раз, хоть он и в ответе, и в черновике.
  expect(screen.getAllByText(NE_OTVECHAET)).toHaveLength(1)
  expect(pole('Белки')).toHaveValue('')
  expect(pole('Состав')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Далее' })).toBeEnabled()

  raspoznat = raspoznano
  await userEvent.click(screen.getByRole('button', { name: 'Распознать ещё раз' }))

  // Пустое поле «Белки» уже на экране — ждём не поле, а прочитанное в нём.
  await waitFor(() => expect(pole('Белки')).toHaveValue('18'))
  expect(screen.queryByText(NE_OTVECHAET)).not.toBeInTheDocument()
  expect(raspoznavaniya).toHaveLength(2)
})

test('лимит распознаваний (429) — текст про лимит, без повтора', async () => {
  const { polzovatel, vperyod } = chasy()
  raspoznat = () => HttpResponse.json({ detail: LIMIT }, { status: 429 })
  await prodolzhit(chernovikNa('label'), polzovatel)

  await polzovatel.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))

  expect(await screen.findByRole('alert')).toHaveTextContent(LIMIT)
  // Поля — вручную; повтор бессмыслен до завтра, кнопки нет.
  expect(pole('Белки')).toHaveValue('')
  expect(screen.queryByRole('button', { name: 'Распознать ещё раз' })).not.toBeInTheDocument()
  await vperyod(10_000)
  expect(raspoznavaniya).toHaveLength(1)
})

test('распознавание не настроено (503) — ручной ввод', async () => {
  raspoznat = () => HttpResponse.json({ detail: NE_NASTROENO }, { status: 503 })
  await prodolzhit(chernovikNa('label'))

  await userEvent.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))

  expect(await screen.findByRole('alert')).toHaveTextContent(NE_NASTROENO)
  expect(screen.queryByRole('button', { name: 'Распознать ещё раз' })).not.toBeInTheDocument()

  await userEvent.type(pole('Белки'), '12,5')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  expect(await screen.findByText('Шаг 6 из 9')).toBeInTheDocument()
  expect(pravki.at(-1)).toMatchObject({ protein: '12,5', fat: null, step: 'approval' })
})

test('распознавание уже идёт (409) — опрос черновика до готовности', async () => {
  const { polzovatel, vperyod } = chasy()
  await prodolzhit(chernovikNa('review', { photos: S_ETIKETKOI }), polzovatel)
  // Распознавание запущено раньше — с другой вкладки или до сна телефона.
  raspoznat = () => {
    chernovik = { ...chernovik!, recognition_status: 'running' }
    return HttpResponse.json({ detail: UZHE_IDYOT }, { status: 409 })
  }
  let posleZapuska = 0
  poOprosu = () => {
    posleZapuska += 1
    if (posleZapuska === 2) raspoznanoNaServere()
  }

  await polzovatel.click(screen.getByRole('button', { name: 'Распознать ещё раз' }))

  // Отказ 409 — и черновик перечитан: в нём «идёт».
  await waitFor(() => expect(posleZapuska).toBe(1))
  expect(await screen.findByText(/Распознаём этикетку/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()

  await vperyod(3000)

  expect(await screen.findByRole('textbox', { name: 'Белки' })).toHaveValue('18')
  expect(posleZapuska).toBe(2)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  // Опрашивается черновик, а распознавание второй раз не запускается.
  expect(raspoznavaniya).toHaveLength(1)

  // Готово — опрос прекращается.
  await vperyod(10_000)
  expect(posleZapuska).toBe(2)
})

test('ответа распознавания не дождались (504) — не ошибка, опрос черновика', async () => {
  const { polzovatel, vperyod } = chasy()
  // nginx не дождался модели, а сервер всё ещё читает этикетку.
  raspoznat = () => {
    chernovik = { ...chernovik!, recognition_status: 'running' }
    return new HttpResponse('<html>504 Gateway Time-out</html>', {
      status: 504,
      headers: { 'Content-Type': 'text/html' },
    })
  }
  await prodolzhit(chernovikNa('label'), polzovatel)
  let posleZapuska = 0

  await polzovatel.upload(screen.getByLabelText('Сфотографировать этикетку'), snimok(4032, 3024))
  expect(await screen.findByText('Шаг 5 из 9')).toBeInTheDocument()
  poOprosu = () => {
    posleZapuska += 1
    if (posleZapuska === 3) raspoznanoNaServere()
  }
  await vperyod(3000)

  expect(screen.getByText(/Распознаём этикетку/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()

  await vperyod(3000)
  await vperyod(3000)

  expect(await screen.findByRole('textbox', { name: 'Белки' })).toHaveValue('18')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(raspoznavaniya).toHaveLength(1)
})

test('распознавание «идёт» дольше предела — поля вручную и «Распознать ещё раз»', async () => {
  const { polzovatel, vperyod } = chasy()
  // Процесс сервера умер посреди чтения: в черновике навсегда «идёт».
  await prodolzhit(
    chernovikNa('review', { photos: S_ETIKETKOI, recognition_status: 'running' }),
    polzovatel,
  )

  expect(await screen.findByText(/Распознаём этикетку/)).toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Белки' })).not.toBeInTheDocument()

  await vperyod(240_000)

  expect(await screen.findByText(/дольше обычного/)).toBeInTheDocument()
  expect(pole('Белки')).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Распознать ещё раз' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Далее' })).toBeEnabled()
})

// ---------------------------------------------------------------------------
// Проверка полей
// ---------------------------------------------------------------------------

test('отказ правки у поля (422) — ошибка у этого поля', async () => {
  await prodolzhit(
    chernovikNa('review', { photos: S_ETIKETKOI, recognition_status: 'done', ...PROCHITANO }),
  )

  await userEvent.clear(pole('Белки'))
  await userEvent.type(pole('Белки'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'Далее' }))

  const belki = pole('Белки')
  expect(await screen.findByText('Белки: «abc» — не число')).toBeInTheDocument()
  expect(belki).toHaveAttribute('aria-invalid', 'true')
  expect(belki).toHaveAccessibleDescription(/Белки: «abc» — не число/)
  // Поле может быть ниже края экрана телефона — фокус ведёт к нему.
  expect(belki).toHaveFocus()
  // Текст — у поля, без второй копии внизу.
  expect(screen.getAllByText('Белки: «abc» — не число')).toHaveLength(1)
  expect(screen.getByText('Шаг 5 из 9')).toBeInTheDocument()
})

test('«Назад» с проверки сохраняет набранное и ведёт к фото — «Переснять»', async () => {
  await prodolzhit(
    chernovikNa('review', { photos: S_ETIKETKOI, recognition_status: 'done', ...PROCHITANO }),
  )

  await userEvent.clear(pole('Изготовитель'))
  await userEvent.type(pole('Изготовитель'), 'Другая сыроварня')
  await userEvent.click(screen.getByRole('button', { name: 'Назад' }))

  expect(await screen.findByText('Шаг 4 из 9')).toBeInTheDocument()
  // Уходит только изменённое: остальное в черновике и так такое же.
  expect(pravki).toEqual([{ manufacturer: 'Другая сыроварня', step: 'label' }])
  // Этикетка уже есть: дальше можно, переснять — тоже.
  expect(screen.getByRole('button', { name: 'Далее' })).toBeEnabled()
  expect(screen.getByLabelText('Переснять')).toBeInTheDocument()
})

test('«Переснять» на проверке — новое фото и новое распознавание, шаг тот же', async () => {
  await prodolzhit(
    chernovikNa('review', {
      photos: S_ETIKETKOI,
      recognition_status: 'failed',
      recognition_error: NE_OTVECHAET,
    }),
  )
  expect(screen.getByRole('alert')).toHaveTextContent(NE_OTVECHAET)

  await userEvent.upload(screen.getByLabelText('Переснять'), snimok(3024, 4032))

  await waitFor(() => expect(pole('Белки')).toHaveValue('18'))
  expect(zagruzki.map((z) => z.foto)).toEqual(['jpeg 1200x1600'])
  expect(raspoznavaniya).toHaveLength(1)
  expect(pravki).toEqual([])
  expect(screen.getByText('Шаг 5 из 9')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByText(ZAMECHANIE)).toBeInTheDocument()
})
