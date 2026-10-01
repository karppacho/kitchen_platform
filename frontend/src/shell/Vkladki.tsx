import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { dostupnye, RAZDELY, type Razdel } from './razdely'

/**
 * Разделы, живущие вкладками внизу телефона: живые, без пометки «скоро».
 * Список тот же, что у меню: `dostupnye` по ролям, затем без `faza`. Своей
 * копии нет: появится живой раздел — появится вкладка; закроют раздел
 * роли — уйдёт и вкладка, а не останется ссылкой на отказ 403.
 */
export function razdelyVkladok(razdely: readonly Razdel[] = RAZDELY): Razdel[] {
  return razdely.filter((r) => !r.faza)
}

// Подписи вкладок короче названий разделов: четыре вкладки делят 360 px.
// Доступное имя остаётся полным — как у пункта меню.
const KOROTKO: Record<string, string> = { '/reconciliation': 'Сверка', '/cards': 'Ингредиент' }

// Поля, при фокусе в которых вкладки прячутся: iOS держит закреплённые
// панели над клавиатурой, и вкладки закрывали бы половину экрана.
const POLYA = 'input, textarea, select'

/**
 * Вкладки — тому, кому есть между чем переключаться. Повару открыт один
 * раздел: единственная вкладка вести никуда не может, полосы внизу нет
 * вовсе, и экран карточки получает всю высоту телефона.
 */
export function Vkladki({ roli }: { roli: readonly string[] }) {
  const [spryatany, spryatat] = useState(false)

  useEffect(() => {
    function vPole(event: FocusEvent) {
      spryatat(event.target instanceof Element && event.target.matches(POLYA))
    }
    function izPolya() {
      spryatat(false)
    }
    document.addEventListener('focusin', vPole)
    document.addEventListener('focusout', izPolya)
    return () => {
      document.removeEventListener('focusin', vPole)
      document.removeEventListener('focusout', izPolya)
    }
  }, [])

  const razdely = razdelyVkladok(dostupnye(RAZDELY, roli))
  if (razdely.length < 2) return null

  return (
    <nav className="vkladki" aria-label="Основные разделы" hidden={spryatany}>
      {razdely.map((r) => {
        const korotko = KOROTKO[r.put]
        return (
          <NavLink
            key={r.put}
            to={r.put}
            aria-label={korotko ? r.nazvanie : undefined}
            className={({ isActive }) => (isActive ? 'vkladka vkladka--tekushchaya' : 'vkladka')}
          >
            {korotko ?? r.nazvanie}
          </NavLink>
        )
      })}
    </nav>
  )
}
