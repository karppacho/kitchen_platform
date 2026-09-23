import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'

import { ApiError } from '../src/api/client'
import { Sostoyanie } from '../src/ui/Sostoyanie'

// Общий <Sostoyanie> используется задачами 11–13, а собственного теста на
// три его состояния до сих пор не было. Пишем здесь: загрузка, ошибка
// (с рабочей кнопкой «Повторить») и 403 (без неё — повторять нечего,
// человек уже представился, и ответ не изменится).

test('во время загрузки показывается «Загрузка…»', () => {
  render(
    <Sostoyanie query={{ isPending: true, isError: false, error: null, refetch: () => {} }} />,
  )
  expect(screen.getByText('Загрузка…')).toBeInTheDocument()
})

test('ошибка показывает сообщение и кнопку «Повторить», нажатие на неё вызывает refetch', async () => {
  const refetch = vi.fn()
  render(
    <Sostoyanie
      query={{
        isPending: false,
        isError: true,
        error: new ApiError(500, 'Сервер ответил, но не смог отдать данные'),
        refetch,
      }}
    />,
  )

  expect(screen.getByText('Не удалось получить данные')).toBeInTheDocument()
  expect(screen.getByText('Сервер ответил, но не смог отдать данные')).toBeInTheDocument()

  const knopka = screen.getByRole('button', { name: 'Повторить' })
  await userEvent.click(knopka)
  expect(refetch).toHaveBeenCalledTimes(1)
})

test('при 403 кнопки «Повторить» нет — человек уже представился, и ответ не изменится', () => {
  render(
    <Sostoyanie
      query={{
        isPending: false,
        isError: true,
        error: new ApiError(403, 'Недостаточно прав'),
        refetch: () => {},
      }}
    />,
  )

  expect(screen.getByText('Доступа нет')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
})
