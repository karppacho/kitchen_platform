import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, expect, test, vi } from 'vitest'

import { poiskIliPusto } from '../src/domain/adres'
import { primenit, type OpisanieKolonki, type Sortirovka, type Vybor } from '../src/domain/tablitsa'
import { PanelTablitsy } from '../src/ui/PanelTablitsy'
import { setViewport } from './setup'

// Панель над таблицей: поиск, «Найдено», фишки включённых фильтров и
// «Сбросить всё»; на узком экране ещё выбор «Сортировка» и «Фильтры» —
// заголовков с кнопками там нет. Состояние — в стенде, группы — из
// настоящего primenit.

type Stroka = { id: string; nazvanie: string; kategoriya: string; tsena: string | null }

const STROKI: Stroka[] = [
  { id: 'B002', nazvanie: 'Маргарита', kategoriya: 'Пицца', tsena: '450.00' },
  { id: 'B001', nazvanie: 'Кетчуп', kategoriya: 'Соус', tsena: '5.52' },
  { id: 'B003', nazvanie: 'Песто', kategoriya: 'Соус', tsena: null },
]

const KOLONKI: OpisanieKolonki<Stroka>[] = [
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.nazvanie } },
  {
    key: 'category',
    title: 'Категория',
    sort: { vid: 'tekst', znachenie: (r) => r.kategoriya },
    filtr: { vid: 'znacheniya', znachenie: (r) => r.kategoriya },
  },
  {
    key: 'price',
    title: 'Цена',
    sort: { vid: 'chislo', znachenie: (r) => r.tsena },
    filtr: {
      vid: 'usloviya',
      usloviya: [
        { kod: 'est', podpis: 'есть', podhodit: (r) => r.tsena !== null },
        { kod: 'net', podpis: 'нет', podhodit: (r) => r.tsena === null },
      ],
    },
  },
  // Ни сортировки, ни фильтра: пунктов в выборе у неё нет.
  { key: 'id', title: 'id' },
]

type SvoystvaStenda = {
  vvodNachalo?: string
  vyborNachalo?: Vybor
  sortirovkaNachalo?: Sortirovka | null
  onSortirovka?: (s: Sortirovka | null) => void
  onVybor?: (kolonka: string, kody: readonly string[]) => void
  onSbrositVsyo?: () => void
}

function Stend({
  vvodNachalo = '',
  vyborNachalo = {},
  sortirovkaNachalo = null,
  onSortirovka = () => {},
  onVybor = () => {},
  onSbrositVsyo = () => {},
}: SvoystvaStenda) {
  const [vvod, zadatVvod] = useState(vvodNachalo)
  const [sortirovka, zadatSortirovku] = useState(sortirovkaNachalo)
  const [vybor, zadatVybor] = useState<Vybor>(vyborNachalo)
  const itog = primenit(STROKI, KOLONKI, { poisk: poiskIliPusto(vvod), sortirovka, vybor }, (r) => [r.nazvanie, r.id])
  return (
    <PanelTablitsy
      kolonki={KOLONKI}
      vvod={vvod}
      onVvod={zadatVvod}
      naideno={itog.stroki.length}
      vsego={itog.vsego}
      sortirovka={sortirovka}
      onSortirovka={(s) => {
        onSortirovka(s)
        zadatSortirovku(s)
      }}
      gruppy={itog.gruppy}
      onVybor={(kolonka, kody) => {
        onVybor(kolonka, kody)
        zadatVybor((prezhniy) => ({ ...prezhniy, [kolonka]: kody }))
      }}
      onSbrositVsyo={() => {
        onSbrositVsyo()
        zadatVvod('')
        zadatVybor({})
      }}
    />
  )
}

beforeEach(() => {
  setViewport(1440)
})

const poisk = () => screen.getByRole('searchbox', { name: 'Поиск по названию или id' })
const vyborSortirovki = () => screen.getByRole('combobox', { name: 'Сортировка' })
const knopkaFiltrov = () => screen.getByRole('button', { name: /^Фильтры/ })
const panelFiltrov = () => screen.getByRole('group', { name: 'Фильтры' })
const fishka = (tekst: string) => screen.getByRole('button', { name: `Снять фильтр «${tekst}»` })
const sbrositVsyo = () => screen.queryByRole('button', { name: 'Сбросить всё' })
const spisokFishek = () => screen.queryByRole('list', { name: 'Активные фильтры' })
// Групп и галочек вне «Фильтров» у панели нет: пусто — значит, панели нет
// в документе. hidden: true — ищем и среди спрятанного (атрибут hidden,
// display: none). Без имени: у спрятанного узла имя для доступности пустое,
// и поиск по имени «Фильтры» его не нашёл бы.
const panelVDokumente = () =>
  screen.queryAllByRole('group', { hidden: true }).length > 0 ||
  screen.queryAllByRole('checkbox', { hidden: true }).length > 0

test('«Найдено: N из M» — в aria-live и следует за поиском', async () => {
  render(<Stend />)
  const naideno = screen.getByText('Найдено: 3 из 3')
  expect(naideno).toHaveAttribute('aria-live', 'polite')
  await userEvent.type(poisk(), 'кетч')
  // Тот же узел, а не новый: программа чтения с экрана зачитывает
  // изменения живой области, а не её появление.
  expect(naideno).toHaveTextContent(/^Найдено: 1 из 3$/)
})

test('поиск из одних пробелов — как пустой: список полный, сбрасывать нечего', async () => {
  render(<Stend />)
  await userEvent.type(poisk(), '   ')
  expect(poisk()).toHaveValue('   ')
  expect(screen.getByText('Найдено: 3 из 3')).toBeInTheDocument()
  expect(sbrositVsyo()).not.toBeInTheDocument()
  expect(spisokFishek()).not.toBeInTheDocument()
})

test('на 1440 px нет выбора сортировки и «Фильтров» — они в шапке таблицы', () => {
  render(<Stend vyborNachalo={{ category: ['Соус'] }} />)
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Фильтры/ })).not.toBeInTheDocument()
  // Фишки — на обеих ширинах.
  expect(fishka('Категория: Соус')).toBeInTheDocument()
})

test('на 360 px — выбор «Сортировка»: «Без сортировки» и по два пункта на колонку', () => {
  setViewport(360)
  render(<Stend />)
  const punkty = within(vyborSortirovki())
    .getAllByRole('option')
    .map((p) => p.textContent)
  expect(punkty).toEqual([
    'Без сортировки',
    'Название: от А до Я',
    'Название: от Я до А',
    'Категория: от А до Я',
    'Категория: от Я до А',
    'Цена: по возрастанию',
    'Цена: по убыванию',
  ])
  expect(vyborSortirovki()).toHaveDisplayValue('Без сортировки')
})

test('выбор сортировки на 360 px — текущая сортировка, смена, «Без сортировки»', async () => {
  setViewport(360)
  const onSortirovka = vi.fn()
  render(<Stend sortirovkaNachalo={{ kolonka: 'category', napravlenie: 'ubyv' }} onSortirovka={onSortirovka} />)
  expect(vyborSortirovki()).toHaveDisplayValue('Категория: от Я до А')
  await userEvent.selectOptions(vyborSortirovki(), 'Цена: по возрастанию')
  expect(vyborSortirovki()).toHaveDisplayValue('Цена: по возрастанию')
  // Сортировка — вид, а не отбор: сбрасывать «всё» из-за неё нечего.
  expect(sbrositVsyo()).not.toBeInTheDocument()
  await userEvent.selectOptions(vyborSortirovki(), 'Без сортировки')
  expect(onSortirovka.mock.calls).toEqual([[{ kolonka: 'price', napravlenie: 'vozr' }], [null]])
})

test('«Фильтры (2)» — число включённых колонок, aria-expanded, внутри все группы', async () => {
  setViewport(360)
  render(<Stend vyborNachalo={{ category: ['Пицца', 'Соус'], price: ['net'] }} />)
  const knopka = screen.getByRole('button', { name: 'Фильтры (2)' })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(knopka)
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(knopka).toHaveAttribute('aria-controls', panelFiltrov().id)
  const gruppy = within(panelFiltrov()).getAllByRole('group')
  expect(gruppy).toEqual([
    within(panelFiltrov()).getByRole('group', { name: 'Категория' }),
    within(panelFiltrov()).getByRole('group', { name: 'Цена' }),
  ])
})

test('без фильтров кнопка — просто «Фильтры», группы в ней все', async () => {
  setViewport(360)
  render(<Stend />)
  await userEvent.click(screen.getByRole('button', { name: 'Фильтры' }))
  expect(within(panelFiltrov()).getByRole('group', { name: 'Категория' })).toBeInTheDocument()
  expect(within(panelFiltrov()).getByRole('group', { name: 'Цена' })).toBeInTheDocument()
})

test('галочка в «Фильтрах» отбирает, панель остаётся открытой', async () => {
  setViewport(360)
  const onVybor = vi.fn()
  render(<Stend onVybor={onVybor} />)
  await userEvent.click(knopkaFiltrov())
  await userEvent.click(within(panelFiltrov()).getByRole('checkbox', { name: 'Соус · 2' }))
  expect(onVybor).toHaveBeenCalledWith('category', ['Соус'])
  expect(within(panelFiltrov()).getByRole('checkbox', { name: 'Соус · 2' })).toBeChecked()
  expect(screen.getByRole('button', { name: 'Фильтры (1)' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('Найдено: 2 из 3')).toBeInTheDocument()
})

test('«Готово» закрывает «Фильтры» и возвращает фокус на кнопку', async () => {
  setViewport(360)
  render(<Stend />)
  await userEvent.click(knopkaFiltrov())
  await userEvent.click(within(panelFiltrov()).getByRole('checkbox', { name: 'Соус · 2' }))
  await userEvent.click(within(panelFiltrov()).getByRole('button', { name: 'Готово' }))
  expect(panelVDokumente()).toBe(false)
  expect(knopkaFiltrov()).toHaveFocus()
  expect(knopkaFiltrov()).toHaveAttribute('aria-expanded', 'false')
})

test('Escape закрывает «Фильтры» и возвращает фокус на кнопку', async () => {
  setViewport(360)
  render(<Stend />)
  await userEvent.click(knopkaFiltrov())
  await userEvent.click(within(panelFiltrov()).getByRole('checkbox', { name: 'есть · 2' }))
  expect(within(panelFiltrov()).getByRole('checkbox', { name: 'есть · 2' })).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(panelVDokumente()).toBe(false)
  expect(knopkaFiltrov()).toHaveFocus()
  expect(knopkaFiltrov()).toHaveAttribute('aria-expanded', 'false')
})

test('закрытых «Фильтров» в документе нет — не спрятаны, а убраны', async () => {
  setViewport(360)
  render(<Stend vyborNachalo={{ category: ['Соус'] }} />)
  expect(panelVDokumente()).toBe(false)
  await userEvent.click(knopkaFiltrov())
  expect(panelVDokumente()).toBe(true)
  await userEvent.click(knopkaFiltrov())
  expect(panelVDokumente()).toBe(false)
})

test('фишка — на каждую включённую колонку, с отмеченными значениями', () => {
  // «Акция» отмечена, но строк у неё нет, — отметка действует, фишка её
  // показывает.
  render(<Stend vyborNachalo={{ category: ['Соус', 'Акция', ''], price: ['net'] }} />)
  const fishki = within(spisokFishek()!).getAllByRole('button')
  expect(fishki.map((f) => f.textContent)).toEqual(['Категория: Акция, Соус, (пусто)', 'Цена: нет'])
  expect(fishki).toEqual([fishka('Категория: Акция, Соус, (пусто)'), fishka('Цена: нет')])
})

test('без включённых фильтров фишек нет', () => {
  render(<Stend vyborNachalo={{ category: [] }} />)
  expect(spisokFishek()).not.toBeInTheDocument()
})

test('на 1440 px × снимает фильтр колонки; фокус — на следующую фишку, фишек нет — на поиск', async () => {
  const onVybor = vi.fn()
  render(<Stend vyborNachalo={{ category: ['Соус'], price: ['net'] }} onVybor={onVybor} />)
  await userEvent.click(fishka('Категория: Соус'))
  expect(onVybor).toHaveBeenLastCalledWith('category', [])
  expect(screen.queryByRole('button', { name: 'Снять фильтр «Категория: Соус»' })).not.toBeInTheDocument()
  expect(fishka('Цена: нет')).toHaveFocus()
  // С клавиатуры — так же.
  await userEvent.keyboard('{Enter}')
  expect(onVybor).toHaveBeenLastCalledWith('price', [])
  expect(spisokFishek()).not.toBeInTheDocument()
  expect(poisk()).toHaveFocus()
})

test.each([1440, 360])('на %i px снятая последняя фишка — фокус на предыдущую', async (shirina) => {
  setViewport(shirina)
  render(<Stend vyborNachalo={{ category: ['Соус'], price: ['net'] }} />)
  await userEvent.click(fishka('Цена: нет'))
  expect(screen.queryByRole('button', { name: 'Снять фильтр «Цена: нет»' })).not.toBeInTheDocument()
  expect(fishka('Категория: Соус')).toHaveFocus()
})

// На телефоне фокус на поле ввода из обработчика касания открывает экранную
// клавиатуру на полэкрана (Chrome на Android фокусирует кнопку при
// касании). Поэтому на узком запасная цель — «Фильтры», а не поиск.
test('на 360 px снятая единственная фишка — фокус на «Фильтры», а не на поиск', async () => {
  setViewport(360)
  render(<Stend vyborNachalo={{ price: ['est'] }} />)
  await userEvent.click(fishka('Цена: есть'))
  expect(spisokFishek()).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Фильтры' })).toHaveFocus()
})

test('на 360 px «Сбросить всё» — фокус на «Фильтры», а не на поиск', async () => {
  setViewport(360)
  render(<Stend vvodNachalo="кетч" vyborNachalo={{ price: ['est'] }} />)
  await userEvent.click(sbrositVsyo()!)
  expect(sbrositVsyo()).not.toBeInTheDocument()
  expect(poisk()).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Фильтры' })).toHaveFocus()
})

test.each([1440, 360])('на %i px фишка и «Сбросить всё» без фокуса на них фокус не трогают', (shirina) => {
  // Касание в iOS и щелчок в macOS кнопку не фокусируют: терять нечего, и
  // переносить фокус незачем. fireEvent.click, в отличие от userEvent,
  // фокуса не ставит.
  setViewport(shirina)
  render(<Stend vvodNachalo="кетч" vyborNachalo={{ category: ['Соус'], price: ['net'] }} />)
  expect(document.body).toHaveFocus()
  fireEvent.click(fishka('Категория: Соус'))
  expect(document.body).toHaveFocus()
  fireEvent.click(fishka('Цена: нет'))
  expect(spisokFishek()).not.toBeInTheDocument()
  expect(document.body).toHaveFocus()
  fireEvent.click(sbrositVsyo()!)
  expect(sbrositVsyo()).not.toBeInTheDocument()
  expect(document.body).toHaveFocus()
})

test('«Сбросить всё» — только при активном поиске или фильтре', async () => {
  const { unmount } = render(<Stend />)
  expect(sbrositVsyo()).not.toBeInTheDocument()
  await userEvent.type(poisk(), 'кетч')
  expect(sbrositVsyo()).toBeInTheDocument()
  await userEvent.clear(poisk())
  expect(sbrositVsyo()).not.toBeInTheDocument()
  unmount()

  render(<Stend vyborNachalo={{ price: ['net'] }} />)
  expect(sbrositVsyo()).toBeInTheDocument()
})

test('на 1440 px «Сбросить всё» зовёт сброс, фокус — на поиск', async () => {
  // Кнопка пропадает вместе с отбором — фокус упал бы на body.
  const onSbrositVsyo = vi.fn()
  render(<Stend vvodNachalo="кетч" vyborNachalo={{ price: ['est'] }} onSbrositVsyo={onSbrositVsyo} />)
  await userEvent.click(sbrositVsyo()!)
  expect(onSbrositVsyo).toHaveBeenCalledTimes(1)
  expect(sbrositVsyo()).not.toBeInTheDocument()
  expect(spisokFishek()).not.toBeInTheDocument()
  expect(poisk()).toHaveValue('')
  expect(poisk()).toHaveFocus()
})
