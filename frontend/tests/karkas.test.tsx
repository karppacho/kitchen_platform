import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { App } from '../src/App'
import { setViewport } from './setup'

test('приложение рисуется', () => {
  render(<App />)
  expect(screen.getByRole('heading')).toBeInTheDocument()
})

test('подмена ширины работает', () => {
  setViewport(360)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(false)
  setViewport(1440)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(true)
})

test('setViewport меняет window.innerWidth', () => {
  setViewport(360)
  expect(window.innerWidth).toBe(360)
  setViewport(1440)
  expect(window.innerWidth).toBe(1440)
})

test('подписчик matchMedia через addEventListener получает matches в change-событии', () => {
  // Так подписываются реальные компоненты (канонический паттерн useWide):
  // mql.addEventListener('change', (e) => setWide(e.matches)). Проверяем
  // именно этот путь, а не чтение matches напрямую, — иначе тест не видит
  // дыру, в которой подписчику вместо matches прилетает undefined.
  const mql = window.matchMedia('(min-width: 1080px)')
  let shirokiy = mql.matches
  mql.addEventListener('change', (e) => {
    shirokiy = (e as MediaQueryListEvent).matches
  })

  setViewport(360)
  expect(shirokiy).toBe(false)

  setViewport(1440)
  expect(shirokiy).toBe(true)
})
