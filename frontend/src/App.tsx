import { Route, Routes } from 'react-router-dom'

import { LoginPage } from './auth/LoginPage'
import { SessionProvider, useSession } from './auth/session'
import { Layout } from './shell/Layout'

/**
 * Гейт защищённых маршрутов. Различает четыре исхода стартовой проверки
 * сессии, а не два: «вошёл» и «не вошёл» — это не всё, что может
 * случиться с `/api/me`.
 */
function RequireAuth() {
  const { me, loading, otkaz, sboy, povtorit } = useSession()

  if (loading) return <p className="zagruzka">Загрузка…</p>

  // Стартовую проверку не удалось выполнить (обрыв сети, 5xx) — это не
  // «не вошёл»: сессия может быть жива, бэкенд просто не ответил. Форма
  // входа здесь означала бы отправлять на неё всех при каждом перезапуске
  // бэкенда, хотя refresh-кука у них ещё жива.
  if (sboy) {
    return (
      <main className="sboy" role="alert">
        <h1>Не удалось проверить сессию</h1>
        <p>{sboy}</p>
        <button type="button" onClick={povtorit}>
          Повторить
        </button>
      </main>
    )
  }

  // Отказ по правам формы входа не показывает: человек уже представился,
  // и повторный вход вернёт ровно то же самое — это круг, из которого он
  // не выйдет.
  if (otkaz) {
    return (
      <main className="otkaz" role="alert">
        <h1>Доступа нет</h1>
        <p>{otkaz}</p>
      </main>
    )
  }

  if (!me) return <LoginPage />

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        {/* Временная заглушка: маршрут /dishes и сам экран блюд добавит
            задача 9. Navigate увёл бы на несуществующий путь и размонтировал
            бы Layout вместе с шапкой. */}
        <Route index element={<p>Блюда</p>} />
      </Route>
    </Routes>
  )
}

export function App() {
  return (
    <SessionProvider>
      <RequireAuth />
    </SessionProvider>
  )
}
