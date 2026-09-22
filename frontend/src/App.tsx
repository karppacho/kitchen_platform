import { Navigate, Route, Routes } from 'react-router-dom'

import { LoginPage } from './auth/LoginPage'
import { SessionProvider, useSession } from './auth/session'
import { DishDetailPage } from './pages/DishDetail'
import { Dishes } from './pages/Dishes'
import { Ingredients } from './pages/Ingredients'
import { Reconciliation } from './pages/Reconciliation'
import { Stub } from './pages/Stub'
import { Layout } from './shell/Layout'
import { RAZDELY } from './shell/razdely'

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
        <Route index element={<Navigate to="/dishes" replace />} />
        <Route path="ingredients" element={<Ingredients />} />
        <Route path="dishes" element={<Dishes />} />
        <Route path="dishes/:legacyId" element={<DishDetailPage />} />
        <Route path="reconciliation" element={<Reconciliation />} />
        {RAZDELY.filter((r) => r.faza).map((r) => (
          <Route key={r.put} path={r.put.slice(1)} element={<Stub />} />
        ))}
        {/* Неизвестный адрес не должен оставлять пустой экран — уводим на
            главный раздел, шапка при этом не размонтируется. */}
        <Route path="*" element={<Navigate to="/dishes" replace />} />
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
