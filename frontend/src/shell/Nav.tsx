import { NavLink } from 'react-router-dom'

import { RAZDELY } from './razdely'

/** Пункт раздела, которого ещё нет: текст фазы («фаза 3», «фаза 4») в
 *  меню не показываем — он повторил бы текст заглушки на той же
 *  странице (Nav не размонтируется при переходе) и сделал бы доступное
 *  имя ссылки двойным. Про фазу человек узнаёт, открыв заглушку. Пункт
 *  просто приглушённого цвета. */
export function Nav({ onGo }: { onGo?: () => void }) {
  return (
    <nav className="menyu" aria-label="Разделы">
      {RAZDELY.map((razdel) => (
        <NavLink
          key={razdel.put}
          to={razdel.put}
          onClick={onGo}
          className={({ isActive }) =>
            [
              'menyu-punkt',
              isActive && 'menyu-punkt--tekushchiy',
              razdel.faza && 'menyu-punkt--budushchiy',
            ]
              .filter(Boolean)
              .join(' ')
          }
        >
          {razdel.nazvanie}
        </NavLink>
      ))}
    </nav>
  )
}
