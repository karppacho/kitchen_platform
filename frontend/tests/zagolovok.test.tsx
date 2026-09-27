import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { primenit, type Sortirovka, type Vybor } from '../src/domain/tablitsa'
import { DataTable, type Column } from '../src/ui/DataTable'
import { ZagolovokKolonki } from '../src/ui/ZagolovokKolonki'
import { setViewport } from './setup'

// Шапка широкой таблицы: у колонки — кнопка сортировки и значок фильтра,
// под значком — панель с галочками. Панель — в портале, поэтому ищем её
// по всему документу, а не внутри таблицы.

type Stroka = { id: string; nazvanie: string; kategoriya: string; tsena: string | null }

const STROKI: Stroka[] = [
  { id: 'B001', nazvanie: 'Кетчуп', kategoriya: 'Соус', tsena: '5.52' },
  { id: 'B002', nazvanie: 'Маргарита', kategoriya: 'Пицца', tsena: '450.00' },
  { id: 'B003', nazvanie: 'Песто', kategoriya: 'Соус', tsena: null },
]

const KOLONKI: Column<Stroka>[] = [
  {
    key: 'name',
    title: 'Название',
    priority: 'always',
    sort: { vid: 'tekst', znachenie: (r) => r.nazvanie },
    render: (r) => r.nazvanie,
  },
  {
    key: 'category',
    title: 'Категория',
    priority: 'wide',
    sort: { vid: 'tekst', znachenie: (r) => r.kategoriya },
    filtr: { vid: 'znacheniya', znachenie: (r) => r.kategoriya },
    render: (r) => r.kategoriya,
  },
  {
    key: 'price',
    title: 'Цена',
    priority: 'always',
    align: 'right',
    sort: { vid: 'chislo', znachenie: (r) => r.tsena },
    filtr: {
      vid: 'usloviya',
      usloviya: [
        { kod: 'est', podpis: 'есть', podhodit: (r) => r.tsena !== null },
        { kod: 'net', podpis: 'нет', podhodit: (r) => r.tsena === null },
      ],
    },
    render: (r) => r.tsena ?? '—',
  },
  // Ни сортировки, ни фильтра — просто заголовок.
  { key: 'id', title: 'Код', priority: 'wide', render: (r) => r.id },
]

type SvoystvaStenda = {
  stroki?: Stroka[]
  nachalo?: Vybor
  sortirovka?: Sortirovka | null
  onSort?: (kolonka: string) => void
  onVybor?: (kolonka: string, kody: readonly string[]) => void
}

/** Таблица с настоящим выбором в состоянии: группы — из `primenit`. */
function Stend({ stroki = STROKI, nachalo = {}, sortirovka = null, onSort = () => {}, onVybor = () => {} }: SvoystvaStenda) {
  const [vybor, zadatVybor] = useState<Vybor>(nachalo)
  const { stroki: vidimye, gruppy } = primenit(stroki, KOLONKI, { poisk: '', sortirovka, vybor }, (r) => [r.nazvanie])
  return (
    <DataTable
      columns={KOLONKI}
      rows={vidimye}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
      sortirovka={sortirovka}
      zagolovok={(k) => (
        <ZagolovokKolonki
          kolonka={k}
          sortirovka={sortirovka}
          onSort={onSort}
          gruppa={gruppy.find((g) => g.kolonka === k.key)}
          onVybor={(kolonka, kody) => {
            onVybor(kolonka, kody)
            zadatVybor((prezhniy) => ({ ...prezhniy, [kolonka]: kody }))
          }}
        />
      )}
    />
  )
}

beforeEach(() => {
  setViewport(1440)
})

const znachok = (imya: string) => screen.getByRole('button', { name: imya })
// Имя значка меняется с числом отмеченного — «Фильтр: Категория, выбрано 1».
const znachokKategorii = () => screen.getByRole('button', { name: /^Фильтр: Категория/ })
const panel = () => screen.getByRole('dialog', { name: 'Фильтр: Категория' })
const galochka = (imya: string) => within(panel()).getByRole('checkbox', { name: imya })

async function otkrytKategoriyu() {
  await userEvent.click(znachokKategorii())
}

test('кнопка сортировки названа заголовком колонки и зовёт onSort', async () => {
  const onSort = vi.fn()
  render(<Stend onSort={onSort} />)
  await userEvent.click(screen.getByRole('button', { name: 'Категория' }))
  await userEvent.click(screen.getByRole('button', { name: 'Цена' }))
  expect(onSort.mock.calls).toEqual([['category'], ['price']])
})

test('колонка без сортировки — просто заголовок, без кнопки', () => {
  render(<Stend />)
  const shapka = screen.getAllByRole('columnheader')[3]!
  expect(shapka).toHaveTextContent('Код')
  expect(within(shapka).queryByRole('button')).not.toBeInTheDocument()
})

// Значок — обычный SVG без подписи (aria-hidden), и порядок программе
// чтения с экрана сообщает aria-sort заголовка. Глазу — только рисунок,
// поэтому рисунок и проверяем: своих тестов у SortirovkaIcon нет.
const OBE_STRELKI = 'M6 8l4-4 4 4M6 12l4 4 4-4'
const VVERH = 'M10 16V4M5 9l5-5 5 5'
const VNIZ = 'M10 4v12M5 11l5 5 5-5'

function risunok(imya: string): string | null | undefined {
  return screen.getByRole('button', { name: imya }).querySelector('svg path')?.getAttribute('d')
}

test('значок сортировки: нет, по возрастанию, по убыванию', () => {
  const { rerender } = render(<Stend />)
  expect(risunok('Категория')).toBe(OBE_STRELKI)
  expect(risunok('Название')).toBe(OBE_STRELKI)

  rerender(<Stend sortirovka={{ kolonka: 'category', napravlenie: 'vozr' }} />)
  expect(risunok('Категория')).toBe(VVERH)
  expect(risunok('Название')).toBe(OBE_STRELKI)

  rerender(<Stend sortirovka={{ kolonka: 'category', napravlenie: 'ubyv' }} />)
  expect(risunok('Категория')).toBe(VNIZ)
  expect(risunok('Название')).toBe(OBE_STRELKI)
})

test('в кнопке сортировки текст — перед значком', () => {
  // Значок первым сдвигает базовую линию кнопки к низу значка, и строка
  // шапки становится выше на 1,5–2 px — на 1920×1080 это минус строка.
  render(<Stend />)
  const knopka = screen.getByRole('button', { name: 'Категория' })
  expect(knopka.firstChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(knopka.firstChild?.textContent).toBe('Категория')
  expect(knopka.lastElementChild?.tagName.toLowerCase()).toBe('svg')
})

test('значок фильтра — только у колонок с фильтром', () => {
  render(<Stend />)
  expect(screen.getAllByRole('button', { name: /^Фильтр/ })).toEqual([
    znachok('Фильтр: Категория'),
    znachok('Фильтр: Цена'),
  ])
  const [nazvanie, kategoriya] = screen.getAllByRole('columnheader')
  expect(within(nazvanie!).getAllByRole('button')).toHaveLength(1)
  expect(within(kategoriya!).getByRole('button', { name: 'Фильтр: Категория' })).toBeInTheDocument()
})

test('значок сообщает, что откроет диалог, и открыт ли он', async () => {
  render(<Stend />)
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-haspopup', 'dialog')
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-expanded', 'false')
  await otkrytKategoriyu()
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-expanded', 'true')
  expect(znachok('Фильтр: Цена')).toHaveAttribute('aria-expanded', 'false')
})

test('в имени значка — сколько отмечено, вместе с отмеченным без строк', () => {
  // У «Акции» строк нет, но она отмечена — и считается.
  render(<Stend nachalo={{ category: ['Акция', 'Пицца'] }} />)
  expect(znachok('Фильтр: Категория, выбрано 2')).toBeInTheDocument()
  expect(znachok('Фильтр: Цена')).toBeInTheDocument()
})

test('закрытой панели в документе нет', () => {
  render(<Stend />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('панель — диалог «Фильтр: Категория» в body, вне обёртки таблицы', async () => {
  // Внутри th её обрезала бы прокрутка обёртки, а липкие th соседних
  // колонок легли бы поверх.
  render(<Stend />)
  await otkrytKategoriyu()
  expect(panel().parentElement).toBe(document.body)
  expect(panel().closest('.tablitsa-obolochka')).toBeNull()
  expect(within(panel()).getByRole('group', { name: 'Категория' })).toBeInTheDocument()
})

test('фокус при открытии — на первой галочке', async () => {
  render(<Stend />)
  await otkrytKategoriyu()
  expect(galochka('Пицца · 1')).toHaveFocus()
})

test('Escape закрывает и возвращает фокус на значок', async () => {
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(znachok('Фильтр: Категория')).toHaveFocus()
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-expanded', 'false')
})

test('«Готово» закрывает и возвращает фокус на значок', async () => {
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.click(within(panel()).getByRole('button', { name: 'Готово' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(znachok('Фильтр: Категория')).toHaveFocus()
})

test('Tab и Shift+Tab ходят по кругу внутри панели', async () => {
  render(<Stend nachalo={{ category: ['Соус'] }} />)
  await otkrytKategoriyu()
  const gotovo = within(panel()).getByRole('button', { name: 'Готово' })
  expect(galochka('Пицца · 1')).toHaveFocus()
  await userEvent.tab()
  expect(galochka('Соус · 2')).toHaveFocus()
  await userEvent.tab()
  expect(within(panel()).getByRole('button', { name: 'Сбросить' })).toHaveFocus()
  await userEvent.tab()
  expect(gotovo).toHaveFocus()
  await userEvent.tab()
  expect(galochka('Пицца · 1')).toHaveFocus()
  await userEvent.tab({ shift: true })
  expect(gotovo).toHaveFocus()
})

test('фокус на самой группе: Tab и Shift+Tab не уводят из панели', async () => {
  // Строк нет, отмечена только «Акция»: снятая, она пропадает, галочек не
  // остаётся — и Galochki ставит фокус на саму группу (tabIndex -1, в
  // порядке Tab её нет). Shift+Tab с неё по порядку документа ушёл бы на
  // значки шапки — вон из панели.
  render(<Stend stroki={[]} nachalo={{ category: ['Акция'] }} />)
  await otkrytKategoriyu()
  expect(galochka('Акция · 0')).toHaveFocus()
  await userEvent.keyboard(' ')
  const gruppa = within(panel()).getByRole('group', { name: 'Категория' })
  expect(gruppa).toHaveFocus()
  const gotovo = within(panel()).getByRole('button', { name: 'Готово' })

  await userEvent.tab({ shift: true })
  expect(gotovo).toHaveFocus()

  // Щелчок по подписи группы возвращает фокус на неё же.
  await userEvent.click(within(gruppa).getByText('Нет значений'))
  expect(gruppa).toHaveFocus()
  await userEvent.tab()
  expect(gotovo).toHaveFocus()
})

test('щелчок по пустому месту панели: клавиатура остаётся в ней', async () => {
  // Иначе фокус ушёл бы на body: Escape не закрыл бы панель, а Tab повёл
  // бы по странице.
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.click(panel())
  await userEvent.tab()
  expect(galochka('Пицца · 1')).toHaveFocus()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(znachok('Фильтр: Категория')).toHaveFocus()
})

test('щелчок снаружи закрывает', async () => {
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.click(document.body)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-expanded', 'false')
})

test('снаружи закрывает уже нажатие, до щелчка', () => {
  // Галочка без строк при снятии исчезает: в браузере React убирает её,
  // пока click ещё всплывает, и проверка «щёлкнули внутри панели» на
  // document ошиблась бы — панель закрылась бы сама. Нажатие приходит,
  // пока галочка на месте. В jsdom обновление применяется уже после всего
  // события (act), поэтому тест ниже про снятую галочку этого не поймает —
  // ловит этот.
  render(<Stend />)
  fireEvent.click(znachok('Фильтр: Категория'))
  fireEvent.pointerDown(document.body)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('снятая галочка без строк исчезает, а панель остаётся открытой', async () => {
  const onVybor = vi.fn()
  render(<Stend nachalo={{ category: ['Акция', 'Соус'] }} onVybor={onVybor} />)
  await otkrytKategoriyu()
  await userEvent.click(galochka('Акция · 0'))
  expect(onVybor).toHaveBeenCalledWith('category', ['Соус'])
  expect(within(panel()).queryByRole('checkbox', { name: 'Акция · 0' })).not.toBeInTheDocument()
  expect(galochka('Пицца · 1')).toHaveFocus()
})

test('повторный щелчок по значку закрывает, а не открывает заново', async () => {
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.click(znachok('Фильтр: Категория'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(znachok('Фильтр: Категория')).toHaveAttribute('aria-expanded', 'false')
})

test('галочка зовёт onVybor, панель остаётся открытой', async () => {
  const onVybor = vi.fn()
  render(<Stend onVybor={onVybor} />)
  await otkrytKategoriyu()
  await userEvent.click(galochka('Соус · 2'))
  expect(onVybor).toHaveBeenCalledWith('category', ['Соус'])
  expect(galochka('Соус · 2')).toBeChecked()
  expect(znachok('Фильтр: Категория, выбрано 1')).toHaveAttribute('aria-expanded', 'true')
  // Фильтр уже сработал: Маргариты в таблице нет.
  expect(screen.queryByRole('cell', { name: 'Маргарита' })).not.toBeInTheDocument()
})

// Размеров в jsdom нет: ширину окна, прокрутку и место значка задаём сами —
// только на время теста, после него свойства возвращаются, как были.
const vernut: (() => void)[] = []

function podmenit(obekt: object, svoystvo: string, znachenie: number) {
  const bylo = Object.getOwnPropertyDescriptor(obekt, svoystvo)
  Object.defineProperty(obekt, svoystvo, { configurable: true, value: znachenie })
  vernut.push(() => {
    if (bylo) Object.defineProperty(obekt, svoystvo, bylo)
    else Reflect.deleteProperty(obekt, svoystvo)
  })
}

afterEach(() => {
  for (const f of vernut.splice(0).reverse()) f()
  vi.restoreAllMocks()
})

function zadatOkno(shirina: number, prokrutkaY: number) {
  podmenit(document.documentElement, 'clientWidth', shirina)
  podmenit(window, 'scrollX', 0)
  podmenit(window, 'scrollY', prokrutkaY)
}

/** Значок 14×14, низ — на 40 px от верха окна. */
function zadatMestoZnachka(knopka: HTMLElement, left: number) {
  const mesto = { left, right: left + 14, top: 26, bottom: 40, width: 14, height: 14, x: left, y: 26 }
  knopka.getBoundingClientRect = () => ({ ...mesto, toJSON: () => mesto })
}

test('панель стоит под значком в координатах документа и едет за ним', async () => {
  zadatOkno(1440, 600)
  render(<Stend />)
  const tsena = znachok('Фильтр: Цена')
  zadatMestoZnachka(tsena, 900)
  await userEvent.click(tsena)
  // «Цена» прижата вправо — панель кончается под правым краем значка.
  const dialog = screen.getByRole('dialog', { name: 'Фильтр: Цена' })
  // Ширина — та, по которой считался прижим к краю окна.
  expect(dialog.style.width).toBe('264px')
  expect(dialog.style.left).toBe(`${914 - 264}px`)
  expect(dialog.style.top).toBe(`${40 + 4 + 600}px`)

  // Таблицу прокрутили вбок внутри обёртки — значок сдвинулся.
  zadatMestoZnachka(tsena, 700)
  fireEvent.scroll(document.querySelector('.tablitsa-obolochka')!)
  expect(dialog.style.left).toBe(`${714 - 264}px`)

  // Окно сузили — значок сдвинулся снова.
  zadatMestoZnachka(tsena, 500)
  fireEvent(window, new Event('resize'))
  expect(dialog.style.left).toBe(`${514 - 264}px`)
})

test('галочка перестроила таблицу — панель едет за значком', async () => {
  // У таблицы нет фиксированной раскладки: фильтр меняет строки, ширины
  // колонок пересчитываются, значок съезжает — без прокрутки и без смены
  // окна. Над таблицей появятся фишки — шапка уедет и вниз.
  zadatOkno(1440, 0)
  render(<Stend />)
  const tsena = znachok('Фильтр: Цена')
  zadatMestoZnachka(tsena, 900)
  await userEvent.click(tsena)
  const dialog = screen.getByRole('dialog', { name: 'Фильтр: Цена' })
  expect(dialog.style.left).toBe(`${914 - 264}px`)

  zadatMestoZnachka(tsena, 600)
  await userEvent.click(within(dialog).getByRole('checkbox', { name: 'есть · 2' }))
  expect(dialog.style.left).toBe(`${614 - 264}px`)
})

test('слушатель нажатия снаружи ставится один раз на открытие', async () => {
  // Каждая галочка перерисовывает панель; снимать и ставить слушатель на
  // каждую незачем.
  const dobavit = vi.spyOn(document, 'addEventListener')
  render(<Stend />)
  await otkrytKategoriyu()
  await userEvent.click(galochka('Соус · 2'))
  await userEvent.click(galochka('Пицца · 1'))
  expect(dobavit.mock.calls.filter(([tip]) => tip === 'pointerdown')).toHaveLength(1)
})

test('имя заголовка колонки — только её название', () => {
  // Программа чтения с экрана называет заголовок на каждом переходе по
  // ячейкам колонки: «Категория», а не «Категория Фильтр: Категория,
  // выбрано 1». Кнопки внутри — со своими именами.
  render(<Stend nachalo={{ category: ['Соус'] }} />)
  const kategoriya = screen.getByRole('columnheader', { name: 'Категория' })
  // Одного имени мало: dom-accessibility-api (jsdom) в имени из содержимого
  // пропускает aria-label вложенных кнопок и дал бы «Категория» и так, а
  // браузеры его берут. Поэтому — и сам aria-label у th.
  expect(kategoriya).toHaveAttribute('aria-label', 'Категория')
  expect(within(kategoriya).getByRole('button', { name: 'Категория' })).toBeInTheDocument()
  expect(within(kategoriya).getByRole('button', { name: 'Фильтр: Категория, выбрано 1' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: 'Цена' })).toHaveAttribute('aria-label', 'Цена')
  expect(screen.getByRole('columnheader', { name: 'Код' })).toHaveAttribute('aria-label', 'Код')
})
