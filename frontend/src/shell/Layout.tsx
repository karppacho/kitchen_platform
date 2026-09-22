import { Outlet } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'

/**
 * Временный минимальный каркас: шапка с именем и ролью плюс место под
 * экран. Полную навигацию и разметку дописывает задача 9 вместе с
 * маршрутом `/dishes` — здесь важно лишь то, что шапка не размонтируется
 * при переходах между защищёнными экранами.
 */
export function Layout() {
  const { me } = useSession()
  return (
    <>
      <header>
        <span>{me?.display_name}</span>
        <span>{me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')}</span>
      </header>
      <Outlet />
    </>
  )
}
