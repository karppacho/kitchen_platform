import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { RAZDELY, type Razdel } from './razdely'

/**
 * Разделы, живущие вкладками внизу телефона: живые, без пометки «скоро».
 * Список тот же, что у меню (`RAZDELY`), своей копии нет: появится живой
 * раздел — появится вкладка. Этап 5 добавит разделам роли и подставит сюда
 * отфильтрованный по роли список — у повара вкладок не будет.
 */
export function razdelyVkladok(razdely: readonly Razdel[] = RAZDELY): Razdel[] {
  return razdely.filter((r) => !r.faza)
}

// Подписи вкладок короче названий разделов: три вкладки делят 360 px.
// Доступное имя остаётся полным — как у пункта меню.
const KOROTKO: Record<string, string> = { '/reconciliation': 'Сверка' }

// Поля, при фокусе в которых вкладки прячутся: iOS держит закреплённые
// панели над клавиатурой, и вкладки закрывали бы половину экрана.
const POLYA = 'input, textarea, select'

export function Vkladki() {
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

  return (
    <nav className="vkladki" aria-label="Основные разделы" hidden={spryatany}>
      {razdelyVkladok().map((r) => {
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
