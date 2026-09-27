import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'vitest'

import { App } from '../src/App'
import { useDishes } from '../src/api/queries'
import { ApiError, api } from '../src/api/client'
import { SessionProvider, useSession } from '../src/auth/session'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
// С задачи 11 /dishes — настоящий экран: любой вход в приложение уводит
// на него (индексный редирект), и он сам вызывает useDishes прямо при
// монтировании. Большинству тестов этого файла всё равно, что вернёт
// /api/dishes, — но без мока запрос ушёл бы неперехваченным
// (onUnhandledRequest: 'bypass' пускает его в настоящую сеть), и его
// непредсказуемое по времени завершение могло бы застать проверку
// сессии где угодно. Регистрируем нейтральный ответ по умолчанию, а
// тесты, которым важен другой ответ /api/dishes (401, 502, обрыв сети),
// перекрывают его собственным server.use — более поздняя регистрация
// побеждает.
//
// То же со строкой свежести (задача 9 синхронизации): она стоит в оболочке
// над каждым экраном и сама ходит в /api/sync. Ответ по умолчанию — свежий;
// тест, которому важен другой, перекрывает его так же.
beforeEach(() => {
  server.use(
    http.get('/api/dishes', () => HttpResponse.json([])),
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
  )
})
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
  // Индексный путь "/" уводит на /dishes (задача 9) — шапка при этом не
  // размонтируется, поэтому проверка на "/" остаётся верной и после
  // появления настоящего маршрута.
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
  const derevo = (
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const { rerender } = render(derevo)

  // Сначала убеждаемся, что сессия действительно жива: если «фоновый»
  // запрос смонтировать сразу, его 401 может прийти раньше профиля, и
  // setMe(profil) затрёт сброс сессии гонкой. Монтируем его только теперь,
  // когда шапка уже показала имя.
  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())

  rerender(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
        <Fon />
      </MemoryRouter>
    </QueryClientProvider>,
  )

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
  const { rerender } = render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())

  rerender(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
        <Fon />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(queries.getQueryState(['dishes', '', ''])?.status).toBe('error'))

  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
  expect(screen.getByText('Алексей')).toBeInTheDocument()
})

// --- Ревью Ruling 38: находки 1 и 2 ---

test('запрос с данными, упавший 401, не мешает повторному входу', async () => {
  // Воспроизводит сценарий из ревью: «шеф смотрит блюда, данные загружены»
  // → «ночью истекает refresh, фоновое обновление получает 401» → «шеф
  // входит снова». Раньше подписчик читал query.state.error (текущее
  // состояние), а не sobytie.action — у запроса с уже загруженными данными
  // старая 401-ошибка не обнулялась действием 'fetch' и могла сорвать
  // самый первый повторный вход.
  //
  // Автозапрос /dishes при монтировании (задача 11) успевает осесть на
  // общем моке по умолчанию из beforeEach ещё до ручного fetchQuery ниже —
  // иначе TanStack Query задедуплицировал бы ручной вызов в тот же, ещё не
  // осевший запрос, и получил бы обрыв сети вместо 401 из мока «ночи».
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
  )

  const queries = novyKlient()
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())

  const kluch = ['dishes', '', '']
  // «Шеф смотрит блюда, данные загружены.»
  queries.setQueryData(kluch, [])
  expect(queries.getQueryState(kluch)?.data).toBeDefined()

  // «Ночью истекает refresh. Фоновое обновление получает 401, сессия
  // сбрасывается.»
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
  )
  // queryClient.fetchQuery вызван напрямую, в обход пользовательских
  // событий — реакт не подхватит вызванное им обновление SessionProvider
  // (setMe(null) внутри подписки) автоматически, оборачиваем сами.
  await act(async () => {
    await expect(
      queries.fetchQuery({ queryKey: kluch, queryFn: () => api('/dishes') }),
    ).rejects.toBeInstanceOf(ApiError)
  })
  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()

  // «Шеф входит.» Первый повторный вход не должен молча сорваться.
  //
  // С задачи 11 экран блюд больше не заглушка: он сам вызывает useDishes и
  // после входа перемонтируется на /dishes, тут же запуская собственный
  // запрос за тем же ключом. Без свежего /api/dishes этот автоматический
  // запрос застал бы старый 401-обработчик и сорвал бы только что открытый
  // вход тем же способом, который тест и проверяет, — поэтому вместе с
  // login добавляем и успешный /api/dishes: в реальности после входа
  // сессия снова живая, и данные экрана приходят как обычно.
  server.use(
    http.post('/api/auth/login', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.get('/api/dishes', () => HttpResponse.json([])),
  )
  await userEvent.type(screen.getByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'пароль')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()

  // «Экран монтируется заново» — тот же запрос запускается ещё раз (если
  // queryClient.clear() не подчистил его при сбросе, в его состоянии
  // всё ещё лежит старая 401-ошибка). Действие 'fetch', а затем 'success'
  // не должны снова сбросить только что открытый вход.
  server.use(http.get('/api/dishes', () => HttpResponse.json([])))
  await act(async () => {
    await queries.refetchQueries({ queryKey: kluch })
  })
  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
  expect(screen.getByText('Алексей')).toBeInTheDocument()
})

function Sonda() {
  // Минимальный потребитель useSession для проверки logout() в отрыве от
  // App/Layout: не нужны ни роуты, ни моки /api/dishes и /api/ingredients,
  // которые тянет за собой полный рендер шапки задачи 9.
  const { me, logout } = useSession()
  return (
    <div>
      <span>{me?.display_name}</span>
      <button onClick={() => void logout()}>Выйти</button>
    </div>
  )
}

test('после выхода кэш запросов пуст', async () => {
  // Без очистки кэша данные экранов ещё staleTime (60 с) показывались бы
  // без перезапроса — в том числе следующему, кто войдёт с этого же
  // устройства (общий кухонный планшет).
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.post('/api/auth/logout', () => new HttpResponse(null, { status: 204 })),
  )

  const queries = novyKlient()
  render(
    <QueryClientProvider client={queries}>
      <SessionProvider>
        <Sonda />
      </SessionProvider>
    </QueryClientProvider>,
  )
  await screen.findByText('Алексей')

  queries.setQueryData(['dishes', '', ''], [])
  queries.setQueryData(['ingredients', '', ''], [])
  expect(queries.getQueryCache().getAll().length).toBeGreaterThan(0)

  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))

  await waitFor(() => expect(queries.getQueryCache().getAll()).toHaveLength(0))
})

// --- Поправка 2: выход работает и без сервера ---

test('502 у ручки выхода не мешает выйти — форма входа, пустой кэш, без необработанного отказа', async () => {
  // Кнопку «Выйти» нажимают на общем кухонном планшете. Если ошибка сети
  // или 502 у /auth/logout остановит очистку кэша и сброс сессии, чужие
  // данные останутся на экране у следующего, кто подойдёт к планшету.
  //
  // Через полный App/Layout (не через изолированный зонд Sonda), как
  // соседние тесты выше: только так проверяется настоящая связь
  // «me === null → RequireAuth рисует форму входа», а не косвенный
  // признак вроде исчезновения имени в изолированном компоненте.
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.post('/api/auth/logout', () => new HttpResponse(null, { status: 502 })),
  )

  const queries = novyKlient()
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())

  // «Шеф до этого смотрел данные экрана» — как в соседнем тесте «после
  // выхода кэш запросов пуст».
  queries.setQueryData(['dishes', '', ''], [])
  expect(queries.getQueryCache().getAll().length).toBeGreaterThan(0)

  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))

  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(queries.getQueryCache().getAll()).toHaveLength(0)
  // Необработанный отказ промиса logout() тест бы не провалил сам по себе —
  // проверка в том, что клик выше вообще не бросил наружу (await дошёл до
  // конца без try/catch в тесте), и что оба следствия (сброс сессии и
  // очистка кэша) выполнились несмотря на 502.
})

// --- Ревью ветки, M11: неподтверждённый выход не молчит ---

const PREDUPREZHDENIE = /Выход не подтверждён сервером/

function voshedshiy() {
  return http.get('/api/me', () =>
    HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
  )
}

async function narisovatVoshedshego(queries = novyKlient()) {
  render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  return queries
}

test('выход с 502 — форма входа, предупреждение и «Повторить выход»', async () => {
  // Куки на сервере живы: следующий на общем планшете перезагрузит
  // страницу, /api/me ответит 200 — и он окажется в сессии шефа.
  server.use(voshedshiy(), http.post('/api/auth/logout', () => new HttpResponse(null, { status: 502 })))

  await narisovatVoshedshego()
  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))

  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(screen.getByText(PREDUPREZHDENIE)).toHaveTextContent(
    'Выход не подтверждён сервером — сессия может быть ещё активна',
  )
  expect(screen.getByRole('button', { name: 'Повторить выход' })).toBeInTheDocument()
})

test('успешный повтор выхода снимает предупреждение', async () => {
  let popytok = 0
  server.use(
    voshedshiy(),
    http.post('/api/auth/logout', () => {
      popytok += 1
      return new HttpResponse(null, { status: popytok === 1 ? 502 : 204 })
    }),
  )

  await narisovatVoshedshego()
  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Повторить выход' }))

  await waitFor(() => expect(screen.queryByText(PREDUPREZHDENIE)).not.toBeInTheDocument())
  expect(screen.queryByRole('button', { name: 'Повторить выход' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Почта')).toBeInTheDocument()
  expect(popytok).toBe(2)
})

test('неудачный повтор выхода предупреждение оставляет', async () => {
  server.use(voshedshiy(), http.post('/api/auth/logout', () => HttpResponse.error()))

  await narisovatVoshedshego()
  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Повторить выход' }))

  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Повторить выход' })).toBeEnabled(),
  )
  expect(screen.getByText(PREDUPREZHDENIE)).toBeInTheDocument()
})

test('обычный успешный выход — без предупреждения', async () => {
  server.use(voshedshiy(), http.post('/api/auth/logout', () => new HttpResponse(null, { status: 204 })))

  await narisovatVoshedshego()
  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))

  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(screen.queryByText(PREDUPREZHDENIE)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Повторить выход' })).not.toBeInTheDocument()
})

test('успешный вход снимает предупреждение о неподтверждённом выходе', async () => {
  server.use(
    voshedshiy(),
    http.post('/api/auth/logout', () => new HttpResponse(null, { status: 502 })),
    http.post('/api/auth/login', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
  )

  const queries = await narisovatVoshedshego()
  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }))
  expect(await screen.findByText(PREDUPREZHDENIE)).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'пароль')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))
  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())

  // Сессия сбрасывается по 401 посреди работы — снова форма входа. Старое
  // предупреждение к этой сессии уже не относится.
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
  )
  await act(async () => {
    await expect(
      queries.fetchQuery({ queryKey: ['dishes', '', ''], queryFn: () => api('/dishes') }),
    ).rejects.toBeInstanceOf(ApiError)
  })
  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(screen.queryByText(PREDUPREZHDENIE)).not.toBeInTheDocument()
})
