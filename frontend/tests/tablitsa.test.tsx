import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'

import { DataTable, type Column } from '../src/ui/DataTable'
import { setViewport } from './setup'

type Blyudo = { id: string; nazvanie: string; kategoriya: string; uc: string }

const stroki: Blyudo[] = [
  { id: 'B003', nazvanie: 'Кетчуп', kategoriya: 'Соус-топпинг', uc: '5,52 ₽' },
]

const kolonki: Column<Blyudo>[] = [
  { key: 'nazvanie', title: 'Название', priority: 'always', render: (r) => r.nazvanie },
  { key: 'uc', title: 'Себестоимость', priority: 'always', align: 'right', render: (r) => r.uc },
  { key: 'kategoriya', title: 'Категория', priority: 'wide', render: (r) => r.kategoriya },
]

function narisovat() {
  return render(
    <DataTable
      columns={kolonki}
      rows={stroki}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
    />,
  )
}

test('на широком экране это настоящая таблица со всеми колонками', () => {
  setViewport(1440)
  narisovat()

  expect(screen.getByRole('table')).toBeInTheDocument()
  expect(screen.getByText('Соус-топпинг')).toBeInTheDocument()
})

test('на 360 px видно только главное', () => {
  // Ширина нужна одной задаче — сравнивать многое сразу. Посмотреть одно
  // блюдо узкому экрану не мешает, и второстепенное там только мешает.
  setViewport(360)
  narisovat()

  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.getByText('Кетчуп')).toBeInTheDocument()
  expect(screen.getByText('5,52 ₽')).toBeInTheDocument()
  expect(screen.queryByText('Соус-топпинг')).not.toBeInTheDocument()
})

test('остальное раскрывается по тапу', async () => {
  setViewport(360)
  narisovat()

  // До тапа второстепенного нет — иначе тест прошла бы и реализация,
  // рисующая всё сразу.
  expect(screen.queryByText('Соус-топпинг')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: /подробнее/i }))

  expect(screen.getByText('Соус-топпинг')).toBeInTheDocument()
})

test('карточку на узком экране можно открыть с клавиатуры', async () => {
  setViewport(360)
  const otkryto: string[] = []
  render(
    <DataTable
      columns={kolonki}
      rows={stroki}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      onOpen={(r) => otkryto.push(r.id)}
    />,
  )

  // Карточка — первый фокусируемый элемент строки; таб на неё, Enter —
  // и onOpen должен сработать, как и на широком экране у <tr>.
  await userEvent.tab()
  expect(screen.getByRole('button', { name: /Кетчуп/i })).toHaveFocus()

  await userEvent.keyboard('{Enter}')

  expect(otkryto).toEqual(['B003'])
})

test('пустой ответ объясняется словами, а не пустотой', () => {
  render(
    <DataTable columns={kolonki} rows={[]} rowKey={(r) => r.id} empty="Ничего не найдено" />,
  )
  expect(screen.getByText('Ничего не найдено')).toBeInTheDocument()
})
