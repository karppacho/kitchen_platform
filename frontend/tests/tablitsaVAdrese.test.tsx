import { act, render } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate, useNavigationType, type NavigateFunction } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import type { OpisanieKolonki } from '../src/domain/tablitsa'
import { useTablitsaVAdrese, type TablitsaVAdrese } from '../src/ui/useTablitsaVAdrese'

// Состояние таблицы живёт в адресе: ссылку шеф шлёт в переписке. Хук
// проверяется на игрушечном компоненте, адрес — зондом рядом с ним.

type Stroka = { name: string; status: string; price: string | null }

const KOLONKI: OpisanieKolonki<Stroka>[] = [
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.name } },
  {
    key: 'status',
    title: 'Статус',
    sort: { vid: 'tekst', znachenie: (r) => r.status },
    filtr: { vid: 'znacheniya', znachenie: (r) => r.status },
  },
  {
    key: 'price',
    title: 'Цена',
    sort: { vid: 'chislo', znachenie: (r) => r.price },
    filtr: {
      vid: 'usloviya',
      usloviya: [
        { kod: 'est', podpis: 'есть', podhodit: (r) => r.price !== null },
        { kod: 'net', podpis: 'нет', podhodit: (r) => r.price === null },
      ],
    },
  },
]

let tablitsa: TablitsaVAdrese
let zond: { search: string; klyuch: string; tip: string; idti: NavigateFunction }

function Igrushka() {
  tablitsa = useTablitsaVAdrese(KOLONKI)
  return null
}

function Zond() {
  const { search, key } = useLocation()
  zond = { search, klyuch: key, tip: useNavigationType(), idti: useNavigate() }
  return null
}

function narisovat(adres = '/') {
  render(
    // Флаги — явно выключенные, как в приложении (BrowserRouter без них):
    // поведение то же, а предупреждение о будущих флагах не печатается.
    <MemoryRouter initialEntries={[adres]} future={{ v7_startTransition: false, v7_relativeSplatPath: false }}>
      <Igrushka />
      <Zond />
    </MemoryRouter>,
  )
}

const parametry = () => [...new URLSearchParams(zond.search)]
const podozhdat = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms)
  })

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

test('состояние читается из адреса, в том числе из старой ссылки', () => {
  narisovat('/?status=архивный&sort=-price&search=соус&price=foo')
  expect(tablitsa.sostoyanie).toEqual({
    poisk: 'соус',
    sortirovka: { kolonka: 'price', napravlenie: 'ubyv' },
    vybor: { status: ['архивный'] },
  })
  expect(tablitsa.vvod).toBe('соус')
})

test('щелчок по заголовку: по возрастанию, по убыванию, выключено; другая колонка — с возрастания', () => {
  narisovat()
  act(() => tablitsa.pereklyuchitSortirovku('name'))
  expect(zond.search).toBe('?sort=name')
  expect(tablitsa.sostoyanie.sortirovka).toEqual({ kolonka: 'name', napravlenie: 'vozr' })
  act(() => tablitsa.pereklyuchitSortirovku('name'))
  expect(zond.search).toBe('?sort=-name')
  act(() => tablitsa.pereklyuchitSortirovku('name'))
  expect(zond.search).toBe('')
  expect(tablitsa.sostoyanie.sortirovka).toBeNull()

  act(() => tablitsa.pereklyuchitSortirovku('name'))
  act(() => tablitsa.pereklyuchitSortirovku('name'))
  expect(zond.search).toBe('?sort=-name')
  act(() => tablitsa.pereklyuchitSortirovku('price'))
  expect(zond.search).toBe('?sort=price')
})

test('выбор «Без сортировки» убирает sort из адреса', () => {
  narisovat('/?sort=-price&status=архивный')
  act(() => tablitsa.zadatSortirovku(null))
  expect(parametry()).toEqual([['status', 'архивный']])
  expect(tablitsa.sostoyanie.sortirovka).toBeNull()
})

test('сортировка и выбор в одном обработчике — в адресе оба', () => {
  // Три записи подряд, без перерисовки между ними: каждая обязана видеть
  // предыдущую, а не параметры, с которыми хук рисовался.
  narisovat('/?search=соус')
  act(() => {
    tablitsa.zadatSortirovku({ kolonka: 'price', napravlenie: 'ubyv' })
    tablitsa.zadatVybor('status', ['архивный'])
    tablitsa.zadatVybor('price', ['net'])
  })
  expect(parametry()).toEqual([
    ['search', 'соус'],
    ['sort', '-price'],
    ['status', 'архивный'],
    ['price', 'net'],
  ])
})

test('поиск: в состоянии сразу, в адресе через 300 мс; фильтр, выбранный в паузу, цел', () => {
  narisovat()
  act(() => tablitsa.zadatVvod('кр'))
  expect(tablitsa.vvod).toBe('кр')
  expect(tablitsa.sostoyanie.poisk).toBe('кр')
  expect(zond.search).toBe('')

  podozhdat(100)
  act(() => tablitsa.zadatVybor('status', ['активный']))
  expect(parametry()).toEqual([['status', 'активный']])
  podozhdat(199)
  expect(parametry()).toEqual([['status', 'активный']])
  podozhdat(1)
  expect(parametry()).toEqual([
    ['search', 'кр'],
    ['status', 'активный'],
  ])
  expect(tablitsa.sostoyanie).toEqual({ poisk: 'кр', sortirovka: null, vybor: { status: ['активный'] } })
})

test('«Сбросить всё» в паузе поиска: ни поиска, ни фильтров, сортировка на месте', () => {
  narisovat('/?sort=-name&status=активный&price=net')
  act(() => tablitsa.zadatVvod('кр'))
  act(() => tablitsa.sbrositVsyo())
  expect(tablitsa.vvod).toBe('')
  expect(parametry()).toEqual([['sort', '-name']])
  // Отложенная запись «кр» не должна вернуться после сброса.
  podozhdat(400)
  expect(parametry()).toEqual([['sort', '-name']])
  expect(tablitsa.sostoyanie).toEqual({ poisk: '', sortirovka: { kolonka: 'name', napravlenie: 'ubyv' }, vybor: {} })

  // Поиск, уже записанный в адрес, сбрасывается тоже.
  act(() => tablitsa.zadatVvod('со'))
  podozhdat(300)
  act(() => tablitsa.zadatVybor('status', ['архивный']))
  expect(parametry()).toEqual([
    ['search', 'со'],
    ['sort', '-name'],
    ['status', 'архивный'],
  ])
  act(() => tablitsa.sbrositVsyo())
  expect(tablitsa.vvod).toBe('')
  expect(parametry()).toEqual([['sort', '-name']])
})

test('запись в адрес заменяет запись истории, а не добавляет новую', () => {
  // Иначе «назад» после десятка щелчков по фильтрам вело бы по каждому из
  // них, а не на прошлый экран.
  narisovat('/?sort=name')
  expect(zond.tip).toBe('POP')
  act(() => tablitsa.zadatVybor('status', ['активный']))
  expect(zond.tip).toBe('REPLACE')
  act(() => zond.idti('/?sort=name'))
  expect(zond.tip).toBe('PUSH')
  act(() => tablitsa.zadatVvod('кр'))
  podozhdat(300)
  expect(zond.search).toContain('search=')
  expect(zond.tip).toBe('REPLACE')
})

test('запись, не меняющая адреса, не навигирует', () => {
  // Лишняя навигация, даже с replace, — новая запись истории со своим key и
  // пустым state: state, с которым пришли на список, пропал бы.
  narisovat('/?sort=name')
  const klyuch = zond.klyuch
  act(() => tablitsa.sbrositVsyo())
  podozhdat(400)
  expect(zond.klyuch).toBe(klyuch)
  expect(zond.tip).toBe('POP')
  act(() => tablitsa.zadatVybor('status', []))
  expect(zond.klyuch).toBe(klyuch)
})

test('адрес, сменённый снаружи, следующая запись не затирает', () => {
  // Ссылка из переписки или «назад» меняют адрес мимо хука.
  narisovat('/?sort=name')
  act(() => zond.idti('/?status=архивный&search=соус'))
  expect(tablitsa.vvod).toBe('соус')
  act(() => tablitsa.pereklyuchitSortirovku('price'))
  expect(parametry()).toEqual([
    ['search', 'соус'],
    ['sort', 'price'],
    ['status', 'архивный'],
  ])
})

test('adresSeychas — с поиском, ещё не записанным в адрес', () => {
  // Адрес для возврата из карточки собирается из текущего состояния: шеф
  // набрал «кр» и сразу открыл блюдо — «← Блюда» должна вернуть и поиск.
  narisovat('/?sort=-price&tab=2')
  expect(tablitsa.adresSeychas()).toBe('?tab=2&sort=-price')
  act(() => tablitsa.zadatVvod('кр'))
  expect(zond.search).toBe('?sort=-price&tab=2')
  const adres = tablitsa.adresSeychas()
  expect(adres.startsWith('?')).toBe(true)
  expect([...new URLSearchParams(adres)]).toEqual([
    ['tab', '2'],
    ['search', 'кр'],
    ['sort', '-price'],
  ])
})

test('zapisatSeychas — поиск в адрес сразу, заменой; тот же адрес — без навигации', () => {
  // Перед уходом в карточку: «назад» браузера вернёт к этой записи истории,
  // и поиск, набранный только что, должен быть уже в ней.
  narisovat('/?sort=-price')
  const klyuch = zond.klyuch
  act(() => tablitsa.zapisatSeychas())
  expect(zond.klyuch).toBe(klyuch)

  act(() => tablitsa.zadatVvod('кр'))
  act(() => tablitsa.zapisatSeychas())
  expect(parametry()).toEqual([
    ['search', 'кр'],
    ['sort', '-price'],
  ])
  expect(zond.tip).toBe('REPLACE')
  // Отложенная запись того же поиска ничего не меняет.
  const posle = zond.klyuch
  podozhdat(400)
  expect(zond.klyuch).toBe(posle)
})

test('adresSeychas пустого состояния — пустая строка', () => {
  narisovat()
  expect(tablitsa.adresSeychas()).toBe('')
  act(() => tablitsa.zadatVvod('   '))
  expect(tablitsa.adresSeychas()).toBe('')
})

test('поиск из одних пробелов — пустой: не активен и в адрес не пишется', () => {
  narisovat('/?sort=name')
  act(() => tablitsa.zadatVvod('   '))
  expect(tablitsa.vvod).toBe('   ')
  expect(tablitsa.sostoyanie.poisk).toBe('')
  podozhdat(400)
  expect(zond.search).toBe('?sort=name')
})
