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
