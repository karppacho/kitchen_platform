import { NavLink } from 'react-router-dom'

import { RAZDELY } from './razdely'

const BUDUSHCHIY_OPISANIE_ID = 'menyu-budushchiy-opisanie'

/**
 * Пункт раздела, которого ещё нет: точный текст фазы («фаза 3», «фаза 4»)
 * в меню не показываем — он повторил бы текст заглушки на той же
 * странице (Nav не размонтируется при переходе). Вместо него два сигнала:
 *
 * - видимая короткая пометка «скоро» в `aria-hidden` — не портит доступное
 *   имя ссылки (оно остаётся ровно `razdel.nazvanie`), но её видит любой
 *   зрячий человек, не только различающий приглушённый цвет;
 * - для программы чтения с экрана — общее скрытое описание «раздел
 *   появится позже», на которое будущие пункты ссылаются через
 *   `aria-describedby`.
 *
 * Приглушённый цвет пункта (`menyu-punkt--budushchiy`) остаётся как
 * дополнительный, необязательный сигнал.
 */
export function Nav({ onGo }: { onGo?: () => void }) {
  return (
    <nav className="menyu" aria-label="Разделы">
      {RAZDELY.map((razdel) => (
        <NavLink
          key={razdel.put}
          to={razdel.put}
          onClick={onGo}
          aria-describedby={razdel.faza ? BUDUSHCHIY_OPISANIE_ID : undefined}
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
          {razdel.faza && (
            <span className="menyu-skoro" aria-hidden="true">
              скоро
            </span>
          )}
        </NavLink>
      ))}
      <p id={BUDUSHCHIY_OPISANIE_ID} className="vizualno-skryto">
        раздел появится позже
      </p>
    </nav>
  )
}
