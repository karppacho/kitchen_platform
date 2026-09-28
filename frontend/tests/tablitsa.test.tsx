import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'

import type { Sortirovka } from '../src/domain/tablitsa'
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

const zagolovok = (nazvanie: string) => screen.getByRole('columnheader', { name: nazvanie })

test('aria-sort — только у отсортированного заголовка', () => {
  // Программа чтения с экрана узнаёт порядок строк только отсюда.
  setViewport(1440)
  const tablitsa = (sortirovka: Sortirovka | null) => (
    <DataTable
      columns={kolonki}
      rows={stroki}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      sortirovka={sortirovka}
    />
  )
  const { rerender } = render(tablitsa({ kolonka: 'uc', napravlenie: 'vozr' }))
  expect(zagolovok('Себестоимость')).toHaveAttribute('aria-sort', 'ascending')
  expect(zagolovok('Название')).not.toHaveAttribute('aria-sort')
  expect(zagolovok('Категория')).not.toHaveAttribute('aria-sort')

  rerender(tablitsa({ kolonka: 'uc', napravlenie: 'ubyv' }))
  expect(zagolovok('Себестоимость')).toHaveAttribute('aria-sort', 'descending')
  expect(zagolovok('Название')).not.toHaveAttribute('aria-sort')

  rerender(tablitsa(null))
  for (const th of screen.getAllByRole('columnheader')) expect(th).not.toHaveAttribute('aria-sort')
})

test('содержимое заголовка — из zagolovok, внутри th', () => {
  setViewport(1440)
  render(
    <DataTable
      columns={kolonki}
      rows={stroki}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      zagolovok={(k) => <button type="button">{k.title}</button>}
    />,
  )
  for (const nazvanie of ['Название', 'Себестоимость', 'Категория']) {
    expect(within(zagolovok(nazvanie)).getByRole('button', { name: nazvanie })).toBeInTheDocument()
  }
})

test('на широком пустой результат не прячет шапку', () => {
  // В шапке сортировка и фильтры: фильтр, отсеявший всё, снимают там же.
  setViewport(1440)
  render(
    <DataTable
      columns={kolonki}
      rows={[]}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      sortirovka={{ kolonka: 'uc', napravlenie: 'ubyv' }}
      zagolovok={(k) => <button type="button">{k.title}</button>}
    />,
  )
  const tablitsa = screen.getByRole('table')
  expect(screen.getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
    'Название',
    'Себестоимость',
    'Категория',
  ])
  expect(within(zagolovok('Название')).getByRole('button')).toBeInTheDocument()
  expect(zagolovok('Себестоимость')).toHaveAttribute('aria-sort', 'descending')
  // Текст — строкой таблицы во всю ширину, под шапкой.
  const yacheyka = within(tablitsa).getByRole('cell', { name: 'Ничего не найдено' })
  expect(yacheyka).toHaveAttribute('colspan', '3')
})

test('на 360 px пустой результат — только текст', () => {
  // Шапки на узком нет, сортировка и фильтры — над списком.
  setViewport(360)
  render(
    <DataTable
      columns={kolonki}
      rows={[]}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      sortirovka={{ kolonka: 'uc', napravlenie: 'ubyv' }}
      zagolovok={(k) => <button type="button">{k.title}</button>}
    />,
  )
  expect(screen.getByText('Ничего не найдено')).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.queryByText('Название')).not.toBeInTheDocument()
})
