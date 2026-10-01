import { Navigate, Route, Routes } from 'react-router-dom'

import { LoginPage } from './auth/LoginPage'
import { SessionProvider, useSession } from './auth/session'
import { DishDetailPage } from './pages/DishDetail'
import { Dishes } from './pages/Dishes'
import { Ingredients } from './pages/Ingredients'
import { NovyiIngredient } from './pages/NovyiIngredient'
import { Reconciliation } from './pages/Reconciliation'
import { Stub } from './pages/Stub'
import { Layout } from './shell/Layout'
import { dostupnye, RAZDELY } from './shell/razdely'

/**
 * Корень и чужой адрес ведут на главную. Ролей нет ни одной — нет и
 * разделов: переадресация крутилась бы по кругу, поэтому говорим словами,
 * не уходя из оболочки (в ней кнопка «Выйти»).
 */
function NaGlavnuyu({ put }: { put: string | undefined }) {
  if (put) return <Navigate to={put} replace />
  return (
    <section>
      <h1>Разделов нет</h1>
      <p>Вашей учётной записи не выдана роль — обратитесь к администратору.</p>
    </section>
  )
}

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

  // Маршруты — только к разделам ролей человека, тем же, что в меню: повар,
  // открывший адрес справочника, попадает в свой раздел, а не на отказ 403.
  const razdely = dostupnye(RAZDELY, me.roles)
  const otkryt = (put: string) => razdely.some((r) => r.put === put)
  // Главная — блюда; кому они закрыты (повару) — его первый раздел.
  const glavnaya = otkryt('/dishes') ? '/dishes' : razdely[0]?.put

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<NaGlavnuyu put={glavnaya} />} />
        {otkryt('/ingredients') && <Route path="ingredients" element={<Ingredients />} />}
        {otkryt('/dishes') && <Route path="dishes" element={<Dishes />} />}
        {otkryt('/dishes') && <Route path="dishes/:legacyId" element={<DishDetailPage />} />}
        {otkryt('/reconciliation') && (
          <Route path="reconciliation" element={<Reconciliation />} />
        )}
        {otkryt('/cards') && <Route path="cards" element={<NovyiIngredient />} />}
        {razdely
          .filter((r) => r.faza)
          .map((r) => (
            <Route key={r.put} path={r.put.slice(1)} element={<Stub />} />
          ))}
        {/* Неизвестный адрес не должен оставлять пустой экран — уводим на
            главный раздел, шапка при этом не размонтируется. */}
        <Route path="*" element={<NaGlavnuyu put={glavnaya} />} />
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
