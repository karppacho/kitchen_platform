import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

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

test('на узком экране меню открывается кнопкой и закрывается после выбора пункта', async () => {
  setViewport(360)
  const { container } = narisovat()
  await screen.findByText('Алексей')

  const knopka = screen.getByRole('button', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  // Кнопка — обычный <button>, поэтому таб-порядок и активация с клавиатуры
  // (Enter/Space) даёт браузер сам, без ручной обвязки.
  expect(knopka.tagName).toBe('BUTTON')

  await userEvent.click(knopka)
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(container.querySelector('.bok')).toHaveClass('bok--otkryt')

  await userEvent.click(screen.getByRole('link', { name: 'Справочник' }))

  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(container.querySelector('.bok')).not.toHaveClass('bok--otkryt')
})

test('шапка показывает обе роли по-русски, включая узкий экран', async () => {
  // Основной случай, не редкий: у настоящего шефа по ТЗ несколько ролей
  // (['chef', 'developer']) — во всех остальных тестах в моках только
  // одна, этот проверяет реальный сценарий входа.
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({
        email: 'chef@example.com',
        display_name: 'Алексей',
        roles: ['chef', 'developer'],
      }),
    ),
  )

  setViewport(360)
  narisovat()

  // jsdom не считает раскладку (нет движка вёрстки) — горизонтальное
  // переполнение шапки на 360 px тут программно не подтвердить, гарантию
  // на этот счёт даёт CSS (overflow-wrap на .shapka-kto в shell.css), не
  // тест. Тест подтверждает то, что можно: обе роли по-русски видны
  // одновременно, а не срезаны логикой рендера.
  expect(await screen.findByText('бренд-шеф, разработчик')).toBeInTheDocument()
})
