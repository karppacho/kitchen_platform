import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { App } from '../src/App'
import { useDishes } from '../src/api/queries'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function novyKlient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function narisovat(putj = '/dishes', queries = novyKlient()) {
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[putj]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('без сессии показывается форма входа', async () => {
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
  )

  narisovat()

  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(screen.queryByText(/зарегистрироваться/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/забыли пароль/i)).not.toBeInTheDocument()
})

test('неверная пара показывается текстом у формы, а не всплывашкой', async () => {
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/login', () =>
      HttpResponse.json({ detail: 'Неверная почта или пароль' }, { status: 401 }),
    ),
  )

  narisovat()
  await userEvent.type(await screen.findByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'не тот')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Неверная почта или пароль')
})

test('лежащая служба входа названа своим именем', async () => {
  // Иначе шеф при лежащем Supabase будет перебирать пароли.
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/login', () =>
      HttpResponse.json({ detail: 'Служба входа не отвечает' }, { status: 502 }),
    ),
  )

  narisovat()
  await userEvent.type(await screen.findByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'пароль')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Служба входа не отвечает')
})

test('403 форму входа не показывает', async () => {
  // Человек уже представился. Повторный вход вернёт ровно то же самое —
  // это круг, из которого он не выйдет.
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ detail: 'профиль не заведён — обратитесь к администратору' }, { status: 403 }),
    ),
  )

  narisovat()

  expect(await screen.findByText(/профиль не заведён/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
  // Без поля пароля тоже: тест обязан ловить и реализацию, которая прячет
  // только поле почты, но оставляет форму целиком.
  expect(screen.queryByLabelText('Пароль')).not.toBeInTheDocument()
})

test('вошедший видит своё имя и роль в шапке', async () => {
  // Маршрута /dishes в этой задаче ещё нет — его добавит задача 9, поэтому
  // здесь проверяем шапку на индексном пути "/", а не на "/dishes".
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.get('/api/dishes', () => HttpResponse.json([])),
  )

  narisovat('/')

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  expect(screen.getByText(/бренд-шеф/i)).toBeInTheDocument()
})

// --- Поправка 3: старт с сетевым сбоем или 5xx — не форма входа ---

test('502 при старте /api/me — сообщение и «Повторить», не форма входа', async () => {
  let popytok = 0
  server.use(
    http.get('/api/me', () => {
      popytok += 1
      if (popytok === 1) return new HttpResponse(null, { status: 502 })
      return HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] })
    }),
  )

  narisovat('/')

  expect(await screen.findByRole('button', { name: 'Повторить' })).toBeInTheDocument()
  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Пароль')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
})

// --- Поправка 4: 401 посреди работы сбрасывает сессию, 5xx — нет ---

function Fon() {
  // Симулирует произвольный запрос экрана, который случится «посреди
  // работы» — использует общий с App QueryClient, поэтому его ошибка
  // видна SessionProvider через кэш запросов.
  useDishes()
  return null
}

test('401 у запроса посреди работы сбрасывает сессию и показывает форму входа', async () => {
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
  )

  const queries = novyKlient()
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
        <Fon />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
})

test('502 у запроса посреди работы сессию не сбрасывает', async () => {
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.get('/api/dishes', () => new HttpResponse('<html>Bad Gateway</html>', { status: 502 })),
  )

  const queries = novyKlient()
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
        <Fon />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  await waitFor(() => expect(queries.getQueryState(['dishes', '', ''])?.status).toBe('error'))

  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
  expect(screen.getByText('Алексей')).toBeInTheDocument()
})
