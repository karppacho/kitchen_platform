import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

let shirina = 1440

/** Задать ширину окна для проверки узкого и широкого вариантов. */
export function setViewport(px: number): void {
  shirina = px
  window.dispatchEvent(new Event('resize'))
}

// jsdom не умеет matchMedia вовсе. Подменяем разбором одного вида запроса —
// (min-width: NNNpx), — которым пользуется useWide. Слушатели вешаются на
// resize, поэтому setViewport перерисовывает компоненты по-настоящему.
beforeEach(() => {
  shirina = 1440
  window.matchMedia = ((query: string): MediaQueryList => {
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0)
    return {
      get matches() {
        return shirina >= min
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, fn: EventListener) =>
        window.addEventListener('resize', fn),
      removeEventListener: (_: string, fn: EventListener) =>
        window.removeEventListener('resize', fn),
      dispatchEvent: () => false,
      addListener: () => {},
      removeListener: () => {},
    } as unknown as MediaQueryList
  }) as typeof window.matchMedia
})

afterEach(cleanup)
