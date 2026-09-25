import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { StrictMode, type ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import { useDishes } from '../src/api/queries'
import type { SyncBook, SyncStatus } from '../src/api/types'
import { Svezhest } from '../src/shell/Svezhest'

const KUHNYA = { book: 'kitchen', title: 'таблица кухни' }
const KARTOCHKI = { book: 'ingredient_cards', title: 'карточки ингредиентов' }

function kniga(chto: Partial<SyncBook> & { book: string; title: string }): SyncBook {
  return {
    checked_at: '2026-09-23T11:58:00Z',
    changed_at: '2026-09-23T11:20:00Z',
    stale: false,
    problem: null,
    problem_since: null,
    ...chto,
  }
}

function svezho(changed_at = '2026-09-23T11:20:00Z'): SyncStatus {
  return {
    data_as_of: '2026-09-23T11:35:00Z',
    changed_at,
    stale: false,
    books: [
      kniga({ ...KUHNYA, checked_at: '2026-09-23T11:35:00Z' }),
      kniga({ ...KARTOCHKI, checked_at: '2026-09-23T11:36:00Z' }),
    ],
  }
}

let otvet: SyncStatus = svezho()
const server = setupServer(http.get('/api/sync', () => HttpResponse.json(otvet)))

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
// Замораживаем только дату: msw и TanStack Query живут на настоящих таймерах.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-23T12:00:00Z')) // 15:00 по Москве
  otvet = svezho()
})
afterEach(() => {
  vi.useRealTimers()
  server.resetHandlers()
})
afterAll(() => server.close())

function nikogda(): SyncStatus {
  return {
    data_as_of: null,
    changed_at: null,
    stale: true,
    books: [
      kniga({ ...KUHNYA, checked_at: null, changed_at: null, stale: true }),
      kniga({ ...KARTOCHKI, checked_at: null, changed_at: null, stale: true }),
    ],
  }
}

function narisovat(deti: ReactNode = <p>экран на месте</p>) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <Svezhest />
      {deti}
    </QueryClientProvider>,
  )
  return queries
}

// Живая область полосы есть всегда (иначе программа чтения с экрана могла бы
// её не объявить) — ждём не её появления, а текста в ней.
function polosa(): Promise<HTMLElement> {
  return waitFor(() => {
    const oblast = screen.getByRole('status')
    expect(oblast).not.toBeEmptyDOMElement()
    return oblast
  })
}

test('свежо — приглушённая строка со временем по Москве, полоса пуста', async () => {
  narisovat()

  expect(await screen.findByText('Данные из таблицы на 14:35')).toBeInTheDocument()
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
})

test('живая область полосы есть и до ответа ручки', () => {
  // Многие программы чтения с экрана объявляют изменения внутри уже
  // существующей области, а область, появившуюся сразу с текстом, пропускают.
  narisovat()

  expect(screen.getByRole('status')).toBeEmptyDOMElement()
})

test('отставшая книга — полоса с временем и причиной, свежая книга в неё не попадает', async () => {
  otvet = {
    ...svezho(),
    data_as_of: '2026-09-23T11:05:00Z',
    stale: true,
    books: [
      kniga({ ...KUHNYA }),
      kniga({
        ...KARTOCHKI,
        checked_at: '2026-09-23T11:05:00Z',
        stale: true,
        problem: 'доступ платформы к таблице закрыт',
        problem_since: '2026-09-23T11:10:00Z',
      }),
    ],
  }
  narisovat()

  const oblast = await polosa()
  expect(oblast).toHaveTextContent(
    'Данные не обновляются с 14:05 — карточки ингредиентов: доступ платформы к таблице закрыт',
  )
  expect(oblast).not.toHaveTextContent('таблица кухни')
})

test('отстала без причины — значит, не работает сам воркер', async () => {
  otvet = { ...svezho(), stale: true, books: [kniga({ ...KUHNYA, checked_at: '2026-09-23T11:05:00Z', stale: true }), kniga({ ...KARTOCHKI })] }
  narisovat()

  expect(await polosa()).toHaveTextContent('синхронизация не запущена')
})

test('ни одной проверки — так и сказано', async () => {
  otvet = nikogda()
  narisovat()

  expect(await polosa()).toHaveTextContent('Данные не синхронизировались — таблица кухни')
})

test('нечего сказать — ни строки, ни жёлтой полосы', async () => {
  // По контракту недостижимо: несверенная книга всегда stale. Но пустая
  // жёлтая полоса хуже никакой — она выглядит как тревога без слов.
  otvet = { ...svezho(), data_as_of: null }
  const queries = narisovat()

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('success'))
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
  expect(screen.getByRole('status')).not.toHaveClass('svezhest--staro')
  expect(screen.queryByText(/Данные/)).not.toBeInTheDocument()
})

test('неразбираемое время не роняет экраны', async () => {
  // Строка рисуется в оболочке над каждым экраном, а error boundary нет:
  // исключение при отрисовке дало бы белый экран во всех разделах.
  otvet = {
    ...svezho(),
    data_as_of: 'не время',
    stale: true,
    books: [kniga({ ...KUHNYA, checked_at: 'не время', stale: true }), kniga({ ...KARTOCHKI })],
  }
  narisovat()

  expect(await polosa()).toHaveTextContent(
    'Данные не обновляются — таблица кухни: синхронизация не запущена',
  )
  expect(screen.getByText('экран на месте')).toBeInTheDocument()
})

test('неразбираемое время свежих данных — строки нет, экран на месте', async () => {
  otvet = { ...svezho(), data_as_of: 'не время' }
  const queries = narisovat()

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('success'))
  expect(screen.queryByText(/Данные/)).not.toBeInTheDocument()
  expect(screen.getByText('экран на месте')).toBeInTheDocument()
})

test('первая выкладка: кухня перенесена, карточки ни разу — полоса только про карточки', async () => {
  // Ruling 30: data_as_of — null, пока хоть одна книга ни разу не сверялась,
  // даже если другая уже перенесена и свежа. Общей фразы про все данные
  // экран по этому null не пишет: полоса называет отставшие книги поимённо.
  otvet = {
    data_as_of: null,
    changed_at: '2026-09-23T11:20:00Z',
    stale: true,
    books: [
      kniga({ ...KUHNYA, checked_at: '2026-09-23T11:58:00Z' }),
      kniga({ ...KARTOCHKI, checked_at: null, changed_at: null, stale: true }),
    ],
  }
  narisovat()

  const oblast = await polosa()
  expect(oblast).toHaveTextContent('Данные не синхронизировались — карточки ингредиентов')
  expect(oblast).not.toHaveTextContent('таблица кухни')
})

test('ручка не ответила — строки нет, экран живёт', async () => {
  server.use(http.get('/api/sync', () => new HttpResponse(null, { status: 500 })))
  const queries = narisovat()

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('error'))
  expect(screen.getByText('экран на месте')).toBeInTheDocument()
  expect(screen.queryByText(/Данные/)).not.toBeInTheDocument()
})

test('опрос упал после успешного — остаётся последняя известная строка', async () => {
  // Время в строке абсолютное, поэтому она не врёт; а исчезай она при
  // разовом сбое опроса — мигала бы на каждом.
  const queries = narisovat()
  await screen.findByText('Данные из таблицы на 14:35')

  server.use(http.get('/api/sync', () => new HttpResponse(null, { status: 500 })))
  await queries.refetchQueries({ queryKey: ['sync'] })

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('error'))
  expect(screen.getByText('Данные из таблицы на 14:35')).toBeInTheDocument()
})

function Proba() {
  useDishes()
  return <p>экран на месте</p>
}

test('сменилось время изменения — открытые экраны перезапрашивают данные', async () => {
  let zaprosov = 0
  server.use(
    http.get('/api/dishes', () => {
      zaprosov += 1
      return HttpResponse.json([])
    }),
  )
  const queries = narisovat(<Proba />)
  await screen.findByText('Данные из таблицы на 14:35')
  await waitFor(() => expect(zaprosov).toBe(1))

  // Тот же changed_at, но ответ другой: строка перерисована, экраны — нет.
  // Ждём, пока новый ответ дойдёт до экрана и затихнут запросы: проверка
  // сразу после refetchQueries прошла бы раньше, чем эффект успел бы
  // перезапросить экраны.
  otvet = { ...svezho(), data_as_of: '2026-09-23T11:40:00Z' }
  await queries.refetchQueries({ queryKey: ['sync'] })
  await screen.findByText('Данные из таблицы на 14:40')
  await waitFor(() => expect(queries.isFetching()).toBe(0))
  expect(zaprosov).toBe(1)

  otvet = svezho('2026-09-23T11:55:00Z')
  await queries.refetchQueries({ queryKey: ['sync'] })
  await waitFor(() => expect(zaprosov).toBe(2))
})

test('первый перенос после выкладки: changed_at был null — экраны перезапрашиваются', async () => {
  // Сайт открыт до первого цикла воркера: changed_at приходит null, потом
  // появляется. null — тоже прежнее значение, и его смена — смена.
  let zaprosov = 0
  server.use(
    http.get('/api/dishes', () => {
      zaprosov += 1
      return HttpResponse.json([])
    }),
  )
  otvet = nikogda()
  const queries = narisovat(<Proba />)
  await polosa()
  await waitFor(() => expect(zaprosov).toBe(1))

  otvet = svezho()
  await queries.refetchQueries({ queryKey: ['sync'] })
  await waitFor(() => expect(zaprosov).toBe(2))
})

test('повтор эффекта при том же времени изменения — не смена, экраны не дёргаются', async () => {
  // Самообновление держится на смене значения changed_at, а не на каждом
  // срабатывании эффекта. Тест выше этого не ловит: при том же значении
  // эффект не перезапускается вовсе. А повтор бывает: приложение рисуется в
  // StrictMode (main.tsx), и React в разработке повторяет эффекты при
  // монтировании. Строка, смонтированная при уже известном времени
  // изменения, приняла бы повтор за смену и перезапросила бы все экраны.
  let zaprosov = 0
  server.use(
    http.get('/api/dishes', () => {
      zaprosov += 1
      return HttpResponse.json([])
    }),
  )
  // Данные уже в кэше и не устаревают — перезапросить их может только смена.
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  queries.setQueryData(['sync'], svezho())
  queries.setQueryData(['dishes', '', ''], [])
  render(
    <StrictMode>
      <QueryClientProvider client={queries}>
        <Svezhest />
        <Proba />
      </QueryClientProvider>
    </StrictMode>,
  )

  expect(screen.getByText('Данные из таблицы на 14:35')).toBeInTheDocument()
  await waitFor(() => expect(queries.isFetching()).toBe(0))
  expect(zaprosov).toBe(0)
})
