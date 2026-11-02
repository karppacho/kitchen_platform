import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { Granitsa } from '../src/shell/Granitsa'

function Lomaetsya(): never {
  throw new Error('нарочно')
}

beforeEach(() => {
  // React и граница пишут ошибку в консоль — в тесте это ожидаемо.
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => vi.restoreAllMocks())

test('исключение экрана даёт сообщение и «Перезагрузить», а не пустоту', () => {
  render(
    <Granitsa>
      <Lomaetsya />
    </Granitsa>,
  )
  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('Экран не открылся')
  expect(screen.getByRole('button', { name: 'Перезагрузить' })).toBeInTheDocument()
})

test('без исключения дети рисуются как есть', () => {
  render(
    <Granitsa>
      <p>Всё в порядке</p>
    </Granitsa>,
  )
  expect(screen.getByText('Всё в порядке')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
