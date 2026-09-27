import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import type { Column } from '../src/ui/DataTable'
import { TablitsaSFiltrami } from '../src/ui/TablitsaSFiltrami'
import { setViewport } from './setup'

// Таблица целиком: состояние в адресе, отбор и сортировка — primenit,
// управление — панель над таблицей и шапка. Адрес видит зонд рядом.

type Blyudo = { id: string; nazvanie: string; kategoriya: string; tsena: string | null }

// Порядок ответа сервера — ни по названию, ни по цене: иначе выключенная
// сортировка была бы неотличима от включённой.
const BLYUDA: Blyudo[] = [
  { id: 'B002', nazvanie: 'Маргарита', kategoriya: 'Пицца', tsena: '450.00' },
  { id: 'B001', nazvanie: 'Кетчуп', kategoriya: 'Соус', tsena: '5.52' },
  { id: 'B004', nazvanie: 'Пепперони', kategoriya: 'Пицца', tsena: '520.00' },
  { id: 'B003', nazvanie: 'Песто', kategoriya: 'Соус', tsena: null },
]

// Константы модуля, как у экранов: TablitsaSFiltrami пересчитывает
// таблицу, когда меняются колонки.
const KOLONKI: Column<Blyudo>[] = [
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
  { key: 'id', title: 'id', priority: 'wide', render: (r) => r.id },
]

const POISK_PO = (r: Blyudo) => [r.nazvanie, r.id]

let adres = ''

function Zond() {
  adres = useLocation().search
  return null
}

// Текст экрана для пустого списка — у каждого экрана свой.
const PUSTO_EKRANA = 'Блюд пока нет'

type Nastroyki = { onOpen?: (r: Blyudo, adresSpiska: string) => void; stroki?: Blyudo[] }

function narisovat(nachalo = '/', { onOpen, stroki = BLYUDA }: Nastroyki = {}) {
  return render(
    // Флаги — явно выключенные, как в приложении: поведение то же, а
    // предупреждение о будущих флагах не печатается.
    <MemoryRouter initialEntries={[nachalo]} future={{ v7_startTransition: false, v7_relativeSplatPath: false }}>
      <TablitsaSFiltrami
        stroki={stroki}
        kolonki={KOLONKI}
        poiskPo={POISK_PO}
        rowKey={(r) => r.id}
        onOpen={onOpen}
        empty={PUSTO_EKRANA}
      />
      <Zond />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  setViewport(1440)
})

// Поиск уходит в адрес через 300 мс после последней буквы. Где важно, что
// он ещё не ушёл, набор и щелчок — синхронным fireEvent: между ними не
// успеет сработать ни один таймер, и «ещё не записан» — правда, а не удача.
// Поддельные таймеры vitest здесь не годятся: Testing Library ждёт после
// каждого действия userEvent настоящего setTimeout и умеет подгонять только
// таймеры jest — тест бы завис.
const polzovatel = () => userEvent.setup()
const podozhdat = (ms: number) => act(() => new Promise((gotovo) => setTimeout(gotovo, ms)))

const parametry = () => [...new URLSearchParams(adres)]
const poisk = () => screen.getByRole('searchbox', { name: 'Поиск по названию или id' })
const nabrat = (tekst: string) => fireEvent.change(poisk(), { target: { value: tekst } })
const zagolovok = (title: string) => screen.getByRole('columnheader', { name: title })
const knopkaSortirovki = (title: string) => within(zagolovok(title)).getByRole('button', { name: title })
const panel = (title: string) => screen.getByRole('dialog', { name: `Фильтр: ${title}` })

// Названия строк сверху вниз — и в таблице, и в списке на узком экране.
const NAZVANIYA = new RegExp(`^(${BLYUDA.map((b) => b.nazvanie).join('|')})$`)
const poryadok = () => screen.queryAllByText(NAZVANIYA).map((el) => el.textContent)
// Подписи галочек — «значение · счётчик», по порядку.
const podpisiGalochek = (gde: HTMLElement) =>
  within(gde)
    .getAllByRole('checkbox')
    .map((g) => g.closest('label')?.textContent)

test('щелчок по заголовку: по возрастанию, по убыванию, порядок ответа', async () => {
  const u = polzovatel()
  narisovat()
  expect(poryadok()).toEqual(['Маргарита', 'Кетчуп', 'Пепперони', 'Песто'])

  // Цена — десятичными строками: как текст «450.00» встало бы раньше «5.52».
  await u.click(knopkaSortirovki('Цена'))
  expect(poryadok()).toEqual(['Кетчуп', 'Маргарита', 'Пепперони', 'Песто'])
  expect(zagolovok('Цена')).toHaveAttribute('aria-sort', 'ascending')
  expect(adres).toBe('?sort=price')

  await u.click(knopkaSortirovki('Цена'))
  expect(poryadok()).toEqual(['Пепперони', 'Маргарита', 'Кетчуп', 'Песто'])
  expect(zagolovok('Цена')).toHaveAttribute('aria-sort', 'descending')
  expect(adres).toBe('?sort=-price')

  await u.click(knopkaSortirovki('Цена'))
  expect(poryadok()).toEqual(['Маргарита', 'Кетчуп', 'Пепперони', 'Песто'])
  expect(zagolovok('Цена')).not.toHaveAttribute('aria-sort')
  expect(adres).toBe('')
})

test('галочка в шапке меняет строки, адрес, «Найдено» и фишки', async () => {
  const u = polzovatel()
  narisovat()
  await u.click(screen.getByRole('button', { name: 'Фильтр: Категория' }))
  await u.click(within(panel('Категория')).getByRole('checkbox', { name: 'Соус · 2' }))
  expect(poryadok()).toEqual(['Кетчуп', 'Песто'])
  expect(parametry()).toEqual([['category', 'Соус']])
  expect(screen.getByText('Найдено: 2 из 4')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Снять фильтр «Категория: Соус»' })).toBeInTheDocument()
})

test('всё отфильтровано: шапка и панель на месте, снятая галочка возвращает строки', async () => {
  const u = polzovatel()
  narisovat('/?category=Пицца&price=net')
  expect(poryadok()).toEqual([])
  // Строки есть, их отсеяли: «Ничего не найдено», а не «Блюд пока нет».
  expect(screen.getByText('Ничего не найдено')).toBeInTheDocument()
  expect(screen.queryByText(PUSTO_EKRANA)).not.toBeInTheDocument()
  expect(screen.getAllByRole('columnheader').map((th) => th.getAttribute('aria-label'))).toEqual([
    'Название',
    'Категория',
    'Цена',
    'id',
  ])
  expect(poisk()).toBeInTheDocument()
  expect(screen.getByText('Найдено: 0 из 4')).toBeInTheDocument()

  await u.click(screen.getByRole('button', { name: 'Фильтр: Цена, выбрано 1' }))
  await u.click(within(panel('Цена')).getByRole('checkbox', { name: 'нет · 0' }))
  expect(poryadok()).toEqual(['Маргарита', 'Пепперони'])
  expect(parametry()).toEqual([['category', 'Пицца']])
})

test('всё отфильтровано на 360 px: «Фильтры» на месте, снятая галочка возвращает строки', async () => {
  setViewport(360)
  const u = polzovatel()
  narisovat('/?category=Пицца&price=net')
  expect(poryadok()).toEqual([])
  expect(screen.getByText('Ничего не найдено')).toBeInTheDocument()
  expect(screen.queryByText(PUSTO_EKRANA)).not.toBeInTheDocument()
  expect(screen.getByText('Найдено: 0 из 4')).toBeInTheDocument()

  await u.click(screen.getByRole('button', { name: 'Фильтры (2)' }))
  const filtry = screen.getByRole('group', { name: 'Фильтры' })
  await u.click(within(filtry).getByRole('checkbox', { name: 'нет · 0' }))
  expect(poryadok()).toEqual(['Маргарита', 'Пепперони'])
  expect(parametry()).toEqual([['category', 'Пицца']])
})

test.each([1440, 360])('на %i px пустой список — текст экрана, а не «Ничего не найдено»', (shirina) => {
  // Ничего не искали и не отбирали — «не найдено» было бы неправдой: строк
  // просто нет. То же, если поиск набран, — искать было не в чем.
  setViewport(shirina)
  narisovat('/', { stroki: [] })
  expect(screen.getByText(PUSTO_EKRANA)).toBeInTheDocument()
  expect(screen.queryByText('Ничего не найдено')).not.toBeInTheDocument()
  expect(screen.getByText('Найдено: 0 из 0')).toBeInTheDocument()
  nabrat('пе')
  expect(screen.getByText(PUSTO_EKRANA)).toBeInTheDocument()
  expect(screen.queryByText('Ничего не найдено')).not.toBeInTheDocument()
})

test('поиск отбирает строки сразу — по названию и по id, в адрес — после паузы', async () => {
  narisovat()
  nabrat('пе')
  expect(poryadok()).toEqual(['Пепперони', 'Песто'])
  expect(screen.getByText('Найдено: 2 из 4')).toBeInTheDocument()
  expect(adres).toBe('')

  nabrat('b004')
  expect(poryadok()).toEqual(['Пепперони'])
  expect(adres).toBe('')
  await podozhdat(350)
  expect(parametry()).toEqual([['search', 'b004']])
})

test('варианты фильтра считаются по строкам, прошедшим поиск', async () => {
  // Как в Google-таблицах: у «Пиццы» после поиска «е» одна строка, а не две.
  const u = polzovatel()
  narisovat()
  await u.type(poisk(), 'е')
  await u.click(screen.getByRole('button', { name: 'Фильтр: Категория' }))
  expect(podpisiGalochek(panel('Категория'))).toEqual(['Пицца · 1', 'Соус · 2'])
})

test('варианты в «Фильтрах» на 360 px — тоже по строкам, прошедшим поиск', async () => {
  setViewport(360)
  const u = polzovatel()
  narisovat()
  await u.type(poisk(), 'е')
  await u.click(screen.getByRole('button', { name: 'Фильтры' }))
  const kategoriya = within(screen.getByRole('group', { name: 'Фильтры' })).getByRole('group', { name: 'Категория' })
  expect(podpisiGalochek(kategoriya)).toEqual(['Пицца · 1', 'Соус · 2'])
})

test('ссылка с параметрами открывает тот же вид', async () => {
  const u = polzovatel()
  narisovat('/?search=е&sort=-name&category=Соус&tab=2')
  expect(poisk()).toHaveValue('е')
  expect(poryadok()).toEqual(['Песто', 'Кетчуп'])
  expect(zagolovok('Название')).toHaveAttribute('aria-sort', 'descending')
  expect(screen.getByText('Найдено: 2 из 4')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Снять фильтр «Категория: Соус»' })).toBeInTheDocument()
  await u.click(screen.getByRole('button', { name: 'Фильтр: Категория, выбрано 1' }))
  expect(within(panel('Категория')).getByRole('checkbox', { name: 'Соус · 2' })).toBeChecked()
  expect(within(panel('Категория')).getByRole('checkbox', { name: 'Пицца · 1' })).not.toBeChecked()
  // Открытие ссылки адреса не меняет.
  expect(parametry()).toEqual([
    ['search', 'е'],
    ['sort', '-name'],
    ['category', 'Соус'],
    ['tab', '2'],
  ])
})

test('«Сбросить всё» снимает поиск и фильтры, сортировку оставляет', async () => {
  const u = polzovatel()
  narisovat('/?search=е&sort=-name&category=Соус')
  await u.click(screen.getByRole('button', { name: 'Сбросить всё' }))
  expect(parametry()).toEqual([['sort', '-name']])
  expect(poisk()).toHaveValue('')
  expect(poryadok()).toEqual(['Песто', 'Пепперони', 'Маргарита', 'Кетчуп'])
  expect(screen.getByText('Найдено: 4 из 4')).toBeInTheDocument()
})

test('«Сбросить всё» в паузе поиска: отложенная запись поиск не вернёт', async () => {
  // Всё — синхронным fireEvent: набранное не успевает уйти в адрес, и сброс
  // застаёт поиск ещё в паузе.
  narisovat()
  nabrat('пе')
  fireEvent.click(screen.getByRole('button', { name: 'Фильтр: Категория' }))
  fireEvent.click(within(panel('Категория')).getByRole('checkbox', { name: 'Пицца · 1' }))
  expect(parametry()).toEqual([['category', 'Пицца']])
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить всё' }))
  expect(parametry()).toEqual([])
  await podozhdat(350)
  expect(parametry()).toEqual([])
  expect(poisk()).toHaveValue('')
  expect(poryadok()).toEqual(['Маргарита', 'Кетчуп', 'Пепперони', 'Песто'])
  expect(screen.queryByRole('list', { name: 'Активные фильтры' })).not.toBeInTheDocument()
})

/** Один и тот же вид, набранный руками: сортировка по цене по убыванию,
    категория «Соус», поиск «е». Возвращает адрес и строки. */
async function naShirokom() {
  setViewport(1440)
  const u = polzovatel()
  const vid = narisovat()
  await u.click(knopkaSortirovki('Цена'))
  await u.click(knopkaSortirovki('Цена'))
  await u.click(screen.getByRole('button', { name: 'Фильтр: Категория' }))
  await u.click(within(panel('Категория')).getByRole('checkbox', { name: 'Соус · 2' }))
  await u.keyboard('{Escape}')
  await u.type(poisk(), 'е')
  await podozhdat(350)
  const itog = { adres, stroki: poryadok() }
  vid.unmount()
  return itog
}

async function naUzkom() {
  setViewport(360)
  const u = polzovatel()
  const vid = narisovat()
  await u.selectOptions(screen.getByRole('combobox', { name: 'Сортировка' }), 'Цена: по убыванию')
  await u.click(screen.getByRole('button', { name: 'Фильтры' }))
  await u.click(within(screen.getByRole('group', { name: 'Фильтры' })).getByRole('checkbox', { name: 'Соус · 2' }))
  await u.click(screen.getByRole('button', { name: 'Готово' }))
  await u.type(poisk(), 'е')
  await podozhdat(350)
  const itog = { adres, stroki: poryadok() }
  vid.unmount()
  return itog
}

test('на узком — тот же адрес и те же строки, что и на широком', async () => {
  // Ссылка, отправленная с телефона, открывает на компьютере тот же вид.
  const shirokiy = await naShirokom()
  expect([...new URLSearchParams(shirokiy.adres)]).toEqual([
    ['search', 'е'],
    ['sort', '-price'],
    ['category', 'Соус'],
  ])
  expect(shirokiy.stroki).toEqual(['Кетчуп', 'Песто'])
  expect(await naUzkom()).toEqual(shirokiy)
})

test.each([1440, 360])('onOpen на %i px получает строку и адрес с поиском, ещё не ушедшим в адрес', async (shirina) => {
  // Шеф набрал «пе» и сразу открыл блюдо: «← Блюда» должна вернуть и поиск.
  setViewport(shirina)
  const onOpen = vi.fn()
  narisovat('/?sort=-price', { onOpen })
  nabrat('пе')
  expect(adres).toBe('?sort=-price')
  // Щелчок по названию всплывает до строки таблицы или кнопки карточки.
  fireEvent.click(screen.getByText('Пепперони'))
  expect(onOpen).toHaveBeenCalledTimes(1)
  const [stroka, adresSpiska] = onOpen.mock.calls[0]!
  expect(stroka).toBe(BLYUDA[2])
  expect(adresSpiska.startsWith('?')).toBe(true)
  expect([...new URLSearchParams(adresSpiska)]).toEqual([
    ['search', 'пе'],
    ['sort', '-price'],
  ])
})
