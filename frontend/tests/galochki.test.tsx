import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { expect, test } from 'vitest'

import { sobratGruppy, type GruppaFiltra, type OpisanieKolonki } from '../src/domain/tablitsa'
import { Galochki } from '../src/ui/Galochki'

// Группа галочек одной колонки — одна на обе ширины: в панели под значком
// фильтра на широком экране и во встроенных «Фильтрах» на узком.

const KATEGORIYA: GruppaFiltra = {
  kolonka: 'category',
  zagolovok: 'Категория',
  aktivna: true,
  varianty: [
    { kod: 'Пицца', podpis: 'Пицца', schyot: 2, vybran: true },
    { kod: 'Соус', podpis: 'Соус', schyot: 3, vybran: false },
    { kod: 'Суп', podpis: 'Суп', schyot: 0, vybran: true },
    { kod: '', podpis: '(пусто)', schyot: 1, vybran: false },
  ],
}

function narisovat(gruppa: GruppaFiltra = KATEGORIYA) {
  const vyzovy: [string, readonly string[]][] = []
  render(<Galochki gruppa={gruppa} onVybor={(kolonka, kody) => vyzovy.push([kolonka, kody])} />)
  return vyzovy
}

const galochka = (imya: string) => screen.getByRole('checkbox', { name: imya })

test('группа названа заголовком, у галочки — значение и счётчик', () => {
  narisovat()
  const gruppa = screen.getByRole('group', { name: 'Категория' })
  expect(within(gruppa).getAllByRole('checkbox')).toEqual([
    galochka('Пицца · 2'),
    galochka('Соус · 3'),
    galochka('Суп · 0'),
    galochka('(пусто) · 1'),
  ])
})

test('отметка — из vybran', () => {
  narisovat()
  expect(galochka('Пицца · 2')).toBeChecked()
  expect(galochka('Соус · 3')).not.toBeChecked()
  // Выбранное без строк — отмечено: с него можно снять галочку.
  expect(galochka('Суп · 0')).toBeChecked()
  expect(galochka('(пусто) · 1')).not.toBeChecked()
})

test('галочка добавляет или снимает свой код, остальные отметки целы', async () => {
  // Внутри колонки значения складываются: «Соус» к «Пицце», а не вместо.
  const vyzovy = narisovat()
  await userEvent.click(galochka('Соус · 3'))
  await userEvent.click(galochka('Пицца · 2'))
  expect(vyzovy).toEqual([
    ['category', ['Пицца', 'Соус', 'Суп']],
    ['category', ['Суп']],
  ])
})

test('«(пусто)» — это код пустой строки', async () => {
  const vyzovy = narisovat()
  await userEvent.click(galochka('(пусто) · 1'))
  expect(vyzovy).toEqual([['category', ['Пицца', 'Суп', '']]])
})

test('«Сбросить» — только у активной группы и снимает все её отметки', async () => {
  const vyzovy = narisovat()
  const gruppa = screen.getByRole('group', { name: 'Категория' })
  await userEvent.click(within(gruppa).getByRole('button', { name: 'Сбросить' }))
  expect(vyzovy).toEqual([['category', []]])
})

test('у неактивной группы «Сбросить» нет', () => {
  narisovat({
    ...KATEGORIYA,
    aktivna: false,
    varianty: KATEGORIYA.varianty.map((v) => ({ ...v, vybran: false })),
  })
  expect(screen.queryByRole('button', { name: 'Сбросить' })).not.toBeInTheDocument()
})

test('группа без значений так и говорит', () => {
  // Поиск или другие колонки отсеяли всё, а в этой ничего не отмечено.
  narisovat({ ...KATEGORIYA, aktivna: false, varianty: [] })
  expect(within(screen.getByRole('group', { name: 'Категория' })).getByText('Нет значений')).toBeInTheDocument()
})

// Настоящая связка: выбор в состоянии, группа — из sobratGruppy.
type Blyudo = { category: string }
const KOLONKI: OpisanieKolonki<Blyudo>[] = [
  { key: 'category', title: 'Категория', filtr: { vid: 'znacheniya', znachenie: (r) => r.category } },
]
const BLYUDA: Blyudo[] = [{ category: 'Пицца' }, { category: 'Пицца' }, { category: 'Соус' }]

function SVyborom({ nachalo }: { nachalo: string[] }) {
  const [vybrano, zadat] = useState<readonly string[]>(nachalo)
  const [gruppa] = sobratGruppy(BLYUDA, KOLONKI, { category: vybrano })
  return <Galochki gruppa={gruppa!} onVybor={(_, kody) => zadat(kody)} />
}

test('отметки копятся и снимаются по одной', async () => {
  render(<SVyborom nachalo={[]} />)
  await userEvent.click(galochka('Пицца · 2'))
  await userEvent.click(galochka('Соус · 1'))
  expect(galochka('Пицца · 2')).toBeChecked()
  expect(galochka('Соус · 1')).toBeChecked()
  await userEvent.click(galochka('Пицца · 2'))
  expect(galochka('Пицца · 2')).not.toBeChecked()
  expect(galochka('Соус · 1')).toBeChecked()
})

test('после «Сбросить» фокус — на первой галочке, а не потерян', async () => {
  // Кнопка пропадает вместе с активностью группы. «Акция» отмечена, но строк
  // у неё нет — после сброса пропадает и она, первой становится «Пицца».
  render(<SVyborom nachalo={['Акция', 'Соус']} />)
  expect(screen.getAllByRole('checkbox')[0]).toBe(galochka('Акция · 0'))
  await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }))
  expect(screen.queryByRole('button', { name: 'Сбросить' })).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox', { name: 'Акция · 0' })).not.toBeInTheDocument()
  expect(galochka('Пицца · 2')).toHaveFocus()
  expect(screen.getAllByRole('checkbox').filter((c) => (c as HTMLInputElement).checked)).toEqual([])
})
