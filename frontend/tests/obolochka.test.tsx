import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test, vi } from 'vitest'

import { App } from '../src/App'
import { RAZDELY } from '../src/shell/razdely'
import { setViewport } from './setup'

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
  ),
  http.get('/api/dishes', () => HttpResponse.json([])),
  http.get('/api/ingredients', () => HttpResponse.json([])),
  http.get('/api/reconciliation', () =>
    HttpResponse.json({ total: 0, linked: 0, needs_human: 0, rows: [] }),
  ),
  // Строка свежести в оболочке сама ходит в /api/sync на каждом экране: без
  // ответа запрос ушёл бы мимо msw в настоящую сеть (Ruling 42).
  http.get('/api/sync', () =>
    HttpResponse.json({
      data_as_of: '2026-09-23T11:35:00Z',
      changed_at: '2026-09-23T11:20:00Z',
      stale: false,
      books: [
        { book: 'kitchen', title: 'таблица кухни', checked_at: '2026-09-23T11:35:00Z', changed_at: '2026-09-23T11:20:00Z', stale: false, problem: null, problem_since: null },
        { book: 'ingredient_cards', title: 'карточки ингредиентов', checked_at: '2026-09-23T11:36:00Z', changed_at: '2026-09-23T09:00:00Z', stale: false, problem: null, problem_since: null },
      ],
    }),
  ),
  // Карточка блюда с задачи 12 сама ходит за данными — этому файлу нужен
  // ответ, а не заглушка, чтобы проверить, что оболочка держит текущим
  // пункт «Блюда» и на вложенном маршруте.
  http.get('/api/dishes/B001', () =>
    HttpResponse.json({
      legacy_id: 'B001',
      name: 'Тестовое блюдо',
      category: 'Блюдо',
      status: 'активное',
      price_menu: '100.00',
      uc_rub: '50.00',
      uc_percent: '50.0',
      margin_percent: '50.0',
      output_grams: '100.000',
      warnings: 0,
      protein_g: '1.0',
      fat_g: '1.0',
      carbs_g: '1.0',
      kcal: '10',
      kbju_coverage: '1.0',
      components: [],
      warning_texts: [],
    }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat(putj = '/dishes') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[putj]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('в меню видны все восемь разделов, включая будущие', async () => {
  // Оболочка проектируется один раз и должна знать про все разделы —
  // иначе меню придётся переделывать при появлении каждого следующего.
  narisovat()
  await screen.findByText('Алексей')

  expect(RAZDELY).toHaveLength(8)
  for (const razdel of RAZDELY) {
    expect(screen.getByRole('link', { name: razdel.nazvanie })).toBeInTheDocument()
  }
})

test('заглушка открывается и говорит, когда раздел появится', async () => {
  narisovat()
  await screen.findByText('Алексей')

  await userEvent.click(screen.getByRole('link', { name: 'Дегустации' }))

  // Меню (Nav) не размонтируется при переходе, а у пункта «Конкуренты»
  // тоже «фаза 4» — ищем текст фазы только внутри содержимого раздела
  // (<main>), а не по всей странице, иначе при нескольких совпадениях
  // текста «фаза 4» тест был бы неустойчив к содержимому меню.
  const soderzhimoe = within(screen.getByRole('main'))
  expect(soderzhimoe.getByRole('heading', { name: 'Дегустации' })).toBeInTheDocument()
  expect(soderzhimoe.getByText(/фаза 4/i)).toBeInTheDocument()
})

test('у будущего пункта меню есть описание для программы чтения с экрана, у готового — нет', async () => {
  narisovat()
  await screen.findByText('Алексей')

  const budushchiy = screen.getByRole('link', { name: 'Дегустации' })
  const opisanieId = budushchiy.getAttribute('aria-describedby')
  expect(opisanieId).toBeTruthy()
  expect(document.getElementById(opisanieId ?? '')).toHaveTextContent('раздел появится позже')

  const gotovyy = screen.getByRole('link', { name: 'Блюда' })
  expect(gotovyy).not.toHaveAttribute('aria-describedby')
})

test('выход есть и он в шапке', async () => {
  narisovat()
  await screen.findByText('Алексей')

  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
})

test('пункт «Блюда» остаётся текущим и на карточке блюда', async () => {
  narisovat('/dishes/B001')
  await screen.findByText('Алексей')

  expect(screen.getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
  // Настоящая карточка (задача 12) показывает имя блюда, а не статичную
  // заглушку — ждём его так же, как ждали бы данные любого запроса.
  expect(await screen.findByRole('heading', { name: 'Тестовое блюдо' })).toBeInTheDocument()
})

test('неизвестный адрес не даёт пустой экран', async () => {
  narisovat('/net-takogo-razdela')
  await screen.findByText('Алексей')

  // Уводит на главный раздел, а не оставляет пустоту: шапка и заголовок
  // раздела на месте.
  expect(screen.getByRole('heading', { name: 'Блюда' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
})

test('строка свежести стоит над экраном', async () => {
  narisovat()
  const stroka = await screen.findByText(/^Данные из таблицы на /)
  const soderzhimoe = screen.getByRole('main')
  const zagolovok = await within(soderzhimoe).findByRole('heading', { level: 1, name: 'Блюда' })

  // В содержимом раздела и раньше заголовка экрана — не в шапке и не под ним.
  expect(soderzhimoe).toContainElement(stroka)
  expect(stroka.compareDocumentPosition(zagolovok) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('на телефоне меню — шторка поверх страницы: открывается кнопкой, закрывается по пункту', async () => {
  setViewport(360)
  narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })

  const knopka = screen.getByRole('button', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  await userEvent.click(knopka)
  const shtorka = screen.getByRole('dialog', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(knopka).toHaveAttribute('aria-controls', shtorka.id)
  // Все восемь разделов — внутри шторки, тем же Nav.
  for (const razdel of RAZDELY) {
    expect(within(shtorka).getByRole('link', { name: razdel.nazvanie })).toBeInTheDocument()
  }

  await userEvent.click(within(shtorka).getByRole('link', { name: 'Справочник' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(await screen.findByRole('heading', { name: 'Справочник ингредиентов' })).toBeInTheDocument()
})

test('на телефоне шторка закрывается по Escape и по нажатию на затемнение', async () => {
  setViewport(360)
  const { baseElement } = narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })
  const knopka = screen.getByRole('button', { name: 'Разделы' })

  await userEvent.click(knopka)
  expect(screen.getByRole('dialog', { name: 'Разделы' })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka).toHaveFocus()

  await userEvent.click(knopka)
  // Затемнение закрывает по click, а не по pointerdown (см. ModalnayaPanel):
  // иначе синтезированный после touchend click попал бы в то, что под ним.
  await userEvent.click(baseElement.querySelector('.modalnaya-fon')!)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('на телефоне имя, роли и «Выйти» — в шторке, а в шапке — название раздела', async () => {
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef', 'developer'] }),
    ),
    http.post('/api/auth/logout', () => new HttpResponse(null, { status: 204 })),
  )
  setViewport(360)
  narisovat('/dishes/B001')
  await screen.findByRole('heading', { name: 'Тестовое блюдо' })

  // У карточки блюда свой <header> внутри <main>. По ARIA это не banner
  // (banner — только header вне секций), но Testing Library это ограничение
  // не учитывает и находит два — берём шапку оболочки: ту, что вне <main>.
  const shapka = screen.getAllByRole('banner').find((el) => el.closest('main') === null)!
  expect(shapka).toHaveTextContent('Блюда')
  expect(within(shapka).queryByRole('button', { name: 'Выйти' })).not.toBeInTheDocument()
  expect(within(shapka).queryByText('Алексей')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Разделы' }))
  const shtorka = screen.getByRole('dialog', { name: 'Разделы' })
  expect(within(shtorka).getByText('Алексей')).toBeInTheDocument()
  expect(within(shtorka).getByText('бренд-шеф, разработчик')).toBeInTheDocument()

  await userEvent.click(within(shtorka).getByRole('button', { name: 'Выйти' }))
  expect(await screen.findByRole('heading', { name: 'Кухня' })).toBeInTheDocument()
})

test('на телефоне внизу вкладки живых разделов, на компьютере их нет', async () => {
  setViewport(360)
  const { unmount } = narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })
  const vkladki = screen.getByRole('navigation', { name: 'Основные разделы' })
  expect(within(vkladki).getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
  expect(within(vkladki).getAllByRole('link')).toHaveLength(3)
  unmount()

  setViewport(1440)
  narisovat()
  await screen.findByText('Алексей')
  expect(screen.queryByRole('navigation', { name: 'Основные разделы' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
})

test('сломавшийся экран не роняет оболочку', async () => {
  // Ответ карточки, от которого экран падает при отрисовке: components не
  // массив. Граница показывает сообщение, шапка и меню остаются.
  vi.spyOn(console, 'error').mockImplementation(() => {})
  server.use(http.get('/api/dishes/B001', () => HttpResponse.json({ legacy_id: 'B001', name: 'Сломанное', components: null })))
  narisovat('/dishes/B001')
  expect(await screen.findByRole('alert')).toHaveTextContent('Экран не открылся')
  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
  vi.restoreAllMocks()
})
