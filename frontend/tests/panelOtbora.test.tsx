import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { expect, test, vi } from 'vitest'

import { poiskIliPusto } from '../src/domain/adres'
import { primenit, type OpisanieKolonki, type Sortirovka, type Vybor } from '../src/domain/tablitsa'
import { PanelOtbora } from '../src/ui/PanelOtbora'

// Панель «Сортировка и фильтры» на телефоне: кнопка открывает диалог снизу,
// в нём переключатели сортировки и группы галочек; отбор применяется сразу,
// «Показать N» только закрывает. Состояние — в стенде, группы — из
// настоящего primenit.

type Stroka = { id: string; nazvanie: string; kategoriya: string; tsena: string | null }
const STROKI: Stroka[] = [
  { id: 'B002', nazvanie: 'Маргарита', kategoriya: 'Пицца', tsena: '450.00' },
  { id: 'B001', nazvanie: 'Кетчуп', kategoriya: 'Соус', tsena: '5.52' },
  { id: 'B003', nazvanie: 'Песто', kategoriya: 'Соус', tsena: null },
]
const KOLONKI: OpisanieKolonki<Stroka>[] = [
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.nazvanie } },
  { key: 'category', title: 'Категория', filtr: { vid: 'znacheniya', znachenie: (r) => r.kategoriya } },
  {
    key: 'price',
    title: 'Цена',
    sort: { vid: 'chislo', znachenie: (r) => r.tsena },
    filtr: { vid: 'usloviya', usloviya: [{ kod: 'est', podpis: 'есть', podhodit: (r) => r.tsena !== null }, { kod: 'net', podpis: 'нет', podhodit: (r) => r.tsena === null }] },
  },
]

function Stend({ onSbrositVsyo = () => {} }: { onSbrositVsyo?: () => void }) {
  const knopka = useRef<HTMLButtonElement>(null)
  const [sortirovka, zadatSortirovku] = useState<Sortirovka | null>(null)
  const [vybor, zadatVybor] = useState<Vybor>({})
  const itog = primenit(STROKI, KOLONKI, { poisk: poiskIliPusto(''), sortirovka, vybor }, (r) => [r.nazvanie])
  return (
    <PanelOtbora
      knopka={knopka}
      kolonki={KOLONKI}
      naideno={itog.stroki.length}
      sortirovka={sortirovka}
      onSortirovka={zadatSortirovku}
      gruppy={itog.gruppy}
      onVybor={(kolonka, kody) => zadatVybor((p) => ({ ...p, [kolonka]: kody }))}
      onSbrositVsyo={() => {
        onSbrositVsyo()
        zadatVybor({})
      }}
    />
  )
}

const knopka = () => screen.getByRole('button', { name: /^Сортировка и фильтры/ })
const dialog = () => screen.getByRole('dialog', { name: 'Сортировка и фильтры' })

test('кнопка открывает панель снизу: сортировка переключателями, группы галочек, «Показать N»', async () => {
  render(<Stend />)
  expect(knopka()).toHaveAttribute('aria-haspopup', 'dialog')
  expect(knopka()).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(knopka())

  const d = dialog()
  expect(knopka()).toHaveAttribute('aria-expanded', 'true')
  expect(knopka()).toHaveAttribute('aria-controls', d.id)
  const sortirovka = within(d).getByRole('group', { name: 'Сортировка' })
  expect(within(sortirovka).getAllByRole('radio').map((r) => r.parentElement?.textContent)).toEqual([
    'Без сортировки',
    'Название: от А до Я',
    'Название: от Я до А',
    'Цена: по возрастанию',
    'Цена: по убыванию',
  ])
  expect(within(sortirovka).getByRole('radio', { name: 'Без сортировки' })).toBeChecked()
  expect(within(d).getByRole('group', { name: 'Категория' })).toBeInTheDocument()
  expect(within(d).getByRole('button', { name: 'Показать 3' })).toBeInTheDocument()
  expect(within(d).queryByRole('button', { name: 'Сбросить всё' })).not.toBeInTheDocument()
})

test('галочка применяется сразу: счётчик «Показать N» и число на кнопке живые; «Сбросить всё» не закрывает', async () => {
  const sbros = vi.fn()
  render(<Stend onSbrositVsyo={sbros} />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('checkbox', { name: 'Соус · 2' }))
  expect(within(dialog()).getByRole('button', { name: 'Показать 2' })).toBeInTheDocument()
  expect(knopka()).toHaveTextContent('Сортировка и фильтры (1)')

  await userEvent.click(within(dialog()).getByRole('button', { name: 'Сбросить всё' }))
  expect(sbros).toHaveBeenCalledTimes(1)
  expect(dialog()).toBeInTheDocument()
  expect(within(dialog()).getByRole('button', { name: 'Показать 3' })).toBeInTheDocument()
})

test('переключатель сортировки меняет сортировку; «Показать» закрывает и возвращает фокус на кнопку', async () => {
  render(<Stend />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('radio', { name: 'Цена: по убыванию' }))
  expect(within(dialog()).getByRole('radio', { name: 'Цена: по убыванию' })).toBeChecked()

  await userEvent.click(within(dialog()).getByRole('button', { name: 'Показать 3' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka()).toHaveFocus()
  expect(knopka()).toHaveAttribute('aria-expanded', 'false')
})

test('включённая группа раскрыта, остальные свёрнуты', async () => {
  render(<Stend />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('checkbox', { name: 'есть · 2' }))
  await userEvent.click(within(dialog()).getByRole('button', { name: 'Показать 2' }))

  await userEvent.click(knopka())
  const details = [...dialog().querySelectorAll('details')]
  expect(details.map((d) => [d.querySelector('summary')?.textContent, d.open])).toEqual([
    ['Категория', false],
    ['Цена · 1', true],
  ])
})
