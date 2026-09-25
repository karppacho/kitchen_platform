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

test('свежо — приглушённая строка со временем по Москве', async () => {
  narisovat()

  expect(await screen.findByText('Данные из таблицы на 14:35')).toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
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

  const polosa = await screen.findByRole('status')
  expect(polosa).toHaveTextContent(
    'Данные не обновляются с 14:05 — карточки ингредиентов: доступ платформы к таблице закрыт',
  )
  expect(polosa).not.toHaveTextContent('таблица кухни')
})

test('отстала без причины — значит, не работает сам воркер', async () => {
  otvet = { ...svezho(), stale: true, books: [kniga({ ...KUHNYA, checked_at: '2026-09-23T11:05:00Z', stale: true }), kniga({ ...KARTOCHKI })] }
  narisovat()

  expect(await screen.findByRole('status')).toHaveTextContent('синхронизация не запущена')
})

test('ни одной проверки — так и сказано', async () => {
  otvet = {
    data_as_of: null,
    changed_at: null,
    stale: true,
    books: [
      kniga({ ...KUHNYA, checked_at: null, changed_at: null, stale: true }),
      kniga({ ...KARTOCHKI, checked_at: null, changed_at: null, stale: true }),
    ],
  }
  narisovat()

  expect(await screen.findByRole('status')).toHaveTextContent(
    'Данные не синхронизировались — таблица кухни',
  )
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

  const polosa = await screen.findByRole('status')
  expect(polosa).toHaveTextContent('Данные не синхронизировались — карточки ингредиентов')
  expect(polosa).not.toHaveTextContent('таблица кухни')
})

test('ручка не ответила — строки нет, экран живёт', async () => {
  server.use(http.get('/api/sync', () => new HttpResponse(null, { status: 500 })))
  const queries = narisovat()

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('error'))
  expect(screen.getByText('экран на месте')).toBeInTheDocument()
  expect(screen.queryByText(/Данные/)).not.toBeInTheDocument()
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

  await queries.refetchQueries({ queryKey: ['sync'] }) // то же время изменения
  expect(zaprosov).toBe(1)

  otvet = svezho('2026-09-23T11:55:00Z')
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
