import { useSyncExternalStore } from 'react'

/** Переключение между таблицей и списком. То же число стоит в table.css. */
export const WIDE = '(min-width: 1080px)'

export function useWide(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const zapros = window.matchMedia(WIDE)
      zapros.addEventListener('change', notify)
      return () => zapros.removeEventListener('change', notify)
    },
    () => window.matchMedia(WIDE).matches,
    // При отрисовке на сервере ширины нет. У нас её нет никогда, но
    // useSyncExternalStore требует третий аргумент.
    () => true,
  )
}
