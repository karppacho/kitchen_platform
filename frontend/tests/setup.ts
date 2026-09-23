import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

let shirina = 1440

/** Подписчик одного медиа-запроса: помним его порог, чтобы слать именно
    ему верный matches, а не общее событие без данных. */
type Podpischik = { min: number; fn: EventListener }
let podpischiki: Podpischik[] = []

/** Задать ширину окна для проверки узкого и широкого вариантов. */
export function setViewport(px: number): void {
  shirina = px
  Object.defineProperty(window, 'innerWidth', { value: px, configurable: true })
  // Канонический подписчик читает e.matches из события 'change', а не
  // общий resize без данных — шлём каждому его собственный результат.
  for (const { min, fn } of podpischiki) {
    fn(Object.assign(new Event('change'), { matches: shirina >= min }))
  }
  window.dispatchEvent(new Event('resize'))
}

// jsdom не умеет matchMedia вовсе. Подменяем разбором одного вида запроса —
// (min-width: NNNpx), — которым пользуется useWide. Каждый matchMedia(...)
// заводит свой порог; setViewport ищет всех подписчиков и шлёт им 'change'
// с matches, посчитанным для их собственного запроса.
beforeEach(() => {
  shirina = 1440
  podpischiki = []
  window.matchMedia = ((query: string): MediaQueryList => {
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0)
    return {
      get matches() {
        return shirina >= min
      },
      media: query,
      onchange: null,
      addEventListener: (type: string, fn: EventListener) => {
        if (type === 'change') podpischiki.push({ min, fn })
      },
      removeEventListener: (type: string, fn: EventListener) => {
        if (type === 'change') podpischiki = podpischiki.filter((p) => p.fn !== fn)
      },
      dispatchEvent: () => false,
      // Легаси-путь подписки (устаревший, но ещё встречается) — тот же
      // список подписчиков, что и у addEventListener/removeEventListener.
      addListener: (fn: EventListener) => podpischiki.push({ min, fn }),
      removeListener: (fn: EventListener) => {
        podpischiki = podpischiki.filter((p) => p.fn !== fn)
      },
    } as unknown as MediaQueryList
  }) as typeof window.matchMedia
})

afterEach(cleanup)
