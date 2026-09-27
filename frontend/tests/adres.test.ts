import { expect, test } from 'vitest'

import {
  kodSortirovki,
  prochitatAdres,
  razobratKodSortirovki,
  zapisatAdres,
} from '../src/domain/adres'
import type { OpisanieKolonki, SostoyanieTablitsy } from '../src/domain/tablitsa'

// Адрес — это ссылка, которую шеф шлёт в переписке: по ней должен
// открыться тот же вид таблицы. Старые ссылки `?status=…&search=…` тоже.

type Blyudo = {
  name: string
  photo: string
  category: string
  status: string
  price: string | null
  uc: string | null
  margin: string | null
}

const K: OpisanieKolonki<Blyudo>[] = [
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.name } },
  { key: 'photo', title: 'Фото' },
  { key: 'category', title: 'Категория', filtr: { vid: 'znacheniya', znachenie: (r) => r.category } },
  { key: 'status', title: 'Статус', filtr: { vid: 'znacheniya', znachenie: (r) => r.status } },
  {
    key: 'price',
    title: 'Цена меню',
    sort: { vid: 'chislo', znachenie: (r) => r.price },
    filtr: {
      vid: 'usloviya',
      usloviya: [
        { kod: 'est', podpis: 'есть', podhodit: (r) => r.price !== null },
        { kod: 'net', podpis: 'нет', podhodit: (r) => r.price === null },
      ],
    },
  },
  {
    key: 'uc',
    title: 'UC ₽',
    filtr: { vid: 'usloviya', usloviya: [{ kod: 'vyshe', podpis: 'выше цены меню', podhodit: () => true }] },
  },
  { key: 'margin', title: 'Маржа %', sort: { vid: 'chislo', znachenie: (r) => r.margin } },
]

const PUSTOE: SostoyanieTablitsy = { poisk: '', sortirovka: null, vybor: {} }
const sostoyanie = (s: Partial<SostoyanieTablitsy>): SostoyanieTablitsy => ({ ...PUSTOE, ...s })
const prochitat = (adres: string) => prochitatAdres(new URLSearchParams(adres), K)
const zapisat = (s: SostoyanieTablitsy, prezhnie = '') => zapisatAdres(new URLSearchParams(prezhnie), s, K)

test('туда и обратно — без потерь', () => {
  const s = sostoyanie({
    poisk: 'соус томатный',
    sortirovka: { kolonka: 'margin', napravlenie: 'ubyv' },
    vybor: { category: ['Блюдо', ''], status: ['архивный'], price: ['est', 'net'], uc: ['vyshe'] },
  })
  // Через строку — именно она попадает в адресную строку.
  expect(prochitat(zapisat(s).toString())).toEqual(s)
  expect(prochitat(zapisat(sostoyanie({ sortirovka: { kolonka: 'name', napravlenie: 'vozr' } })).toString())).toEqual(
    sostoyanie({ sortirovka: { kolonka: 'name', napravlenie: 'vozr' } }),
  )
})

test('пустое состояние — пустой адрес', () => {
  expect(zapisat(PUSTOE).toString()).toBe('')
  // Свои параметры, оставшиеся от прошлого вида, стираются.
  expect(zapisat(PUSTOE, 'search=соус&sort=name&category=Блюдо&uc=vyshe').toString()).toBe('')
  expect(prochitat('')).toEqual(PUSTOE)
})

test('порядок параметров не зависит от порядка выбора', () => {
  const a = sostoyanie({
    poisk: 'соус',
    sortirovka: { kolonka: 'margin', napravlenie: 'ubyv' },
    vybor: { uc: ['vyshe'], category: ['Соус-топпинг', 'Блюдо'] },
  })
  const b = sostoyanie({
    poisk: 'соус',
    sortirovka: { kolonka: 'margin', napravlenie: 'ubyv' },
    vybor: { category: ['Блюдо', 'Соус-топпинг'], uc: ['vyshe'] },
  })
  // Поиск, сортировка, затем фильтры в порядке колонок; значения — как в
  // списке галочек, условия — в объявленном порядке.
  expect([...zapisat(a)]).toEqual([
    ['search', 'соус'],
    ['sort', '-margin'],
    ['category', 'Блюдо'],
    ['category', 'Соус-топпинг'],
    ['uc', 'vyshe'],
  ])
  expect(zapisat(b).toString()).toBe(zapisat(a).toString())
  expect([...zapisat(sostoyanie({ vybor: { price: ['net', 'est'], category: ['', 'Соус'] } }))]).toEqual([
    ['category', 'Соус'],
    ['category', ''],
    ['price', 'est'],
    ['price', 'net'],
  ])
})

test('чужие параметры целы', () => {
  const s = sostoyanie({ vybor: { category: ['Новое'] } })
  expect([...zapisat(s, 'tab=2&category=Старое&utm=a&sort=name&utm=b')]).toEqual([
    ['tab', '2'],
    ['utm', 'a'],
    ['utm', 'b'],
    ['category', 'Новое'],
  ])
  // Колонка без фильтра параметра не пишет — одноимённый параметр чужой.
  expect([...zapisat(PUSTOE, 'name=кто-то&photo=1')]).toEqual([
    ['name', 'кто-то'],
    ['photo', '1'],
  ])
})

test('повторы схлопнуты', () => {
  expect(prochitat('category=Соус&category=Блюдо&category=Соус&price=net&price=net').vybor).toEqual({
    category: ['Блюдо', 'Соус'],
    price: ['net'],
  })
  expect([...zapisat(sostoyanie({ vybor: { category: ['Соус', 'Соус'], uc: ['vyshe', 'vyshe'] } }))]).toEqual([
    ['category', 'Соус'],
    ['uc', 'vyshe'],
  ])
})

test('пустое значение — «(пусто)»: `category=` → [""]', () => {
  expect(prochitat('category=').vybor).toEqual({ category: [''] })
  expect(zapisat(sostoyanie({ vybor: { category: [''] } })).toString()).toBe('category=')
})

test('неизвестные колонка и код отброшены', () => {
  // Сортировка по чужой колонке и по колонке без сортировки.
  expect(prochitat('sort=nope').sortirovka).toBeNull()
  expect(prochitat('sort=-photo').sortirovka).toBeNull()
  // Параметр неизвестной колонки и колонки без фильтра — не выбор.
  expect(prochitat('foo=bar&name=Соус&photo=1').vybor).toEqual({})
  // И обратно: запись их не пишет.
  expect(
    zapisat(
      sostoyanie({ sortirovka: { kolonka: 'nope', napravlenie: 'vozr' }, vybor: { foo: ['bar'], name: ['Соус'] } }),
    ).toString(),
  ).toBe('')
  expect(zapisat(sostoyanie({ sortirovka: { kolonka: 'photo', napravlenie: 'ubyv' } })).toString()).toBe('')
})

test('неизвестный код условия отброшен, значение из списка — любое', () => {
  // Ссылка с опечаткой иначе дала бы пустую таблицу с активным фильтром и
  // ни одной отмеченной галочкой.
  expect(prochitat('price=foo').vybor).toEqual({})
  expect(prochitat('price=foo&price=est&uc=est').vybor).toEqual({ price: ['est'] })
  expect([...zapisat(sostoyanie({ vybor: { price: ['foo', 'net'], uc: ['net'] } }))]).toEqual([['price', 'net']])
  // Значения списка берутся из ответа сервера, заранее их не знает никто:
  // годится любое, даже которого сейчас нет ни в одной строке.
  expect(prochitat('category=Такой нет&status=удалённое').vybor).toEqual({
    category: ['Такой нет'],
    status: ['удалённое'],
  })
})

test('`sort=-` и `sort=` игнорируются', () => {
  expect(prochitat('sort=-').sortirovka).toBeNull()
  expect(prochitat('sort=').sortirovka).toBeNull()
  expect(razobratKodSortirovki('-')).toBeNull()
  expect(razobratKodSortirovki('')).toBeNull()
})

test('коды сортировки: x — по возрастанию, -x — по убыванию', () => {
  expect(kodSortirovki({ kolonka: 'margin', napravlenie: 'ubyv' })).toBe('-margin')
  expect(kodSortirovki({ kolonka: 'name', napravlenie: 'vozr' })).toBe('name')
  expect(razobratKodSortirovki('-margin')).toEqual({ kolonka: 'margin', napravlenie: 'ubyv' })
  expect(razobratKodSortirovki('name')).toEqual({ kolonka: 'name', napravlenie: 'vozr' })
  expect(prochitat('sort=-margin').sortirovka).toEqual({ kolonka: 'margin', napravlenie: 'ubyv' })
})

test('старая ссылка `?status=архивный&search=…` — выбор статуса и поиск', () => {
  expect(prochitat('status=архивный&search=соус')).toEqual(
    sostoyanie({ poisk: 'соус', vybor: { status: ['архивный'] } }),
  )
})

test('кириллица и `&` в значении не ломают адрес', () => {
  const s = sostoyanie({ poisk: 'хлеб & соль', vybor: { category: ['Соус & топпинг', 'Блюдо'] } })
  const stroka = zapisat(s).toString()
  expect(stroka).toContain('%26')
  expect(prochitat(stroka)).toEqual(sostoyanie({ poisk: 'хлеб & соль', vybor: { category: ['Блюдо', 'Соус & топпинг'] } }))
})

test('поиск из одних пробелов — пустой: не пишется и не читается', () => {
  expect(zapisat(sostoyanie({ poisk: '   ' })).toString()).toBe('')
  expect(zapisat(sostoyanie({ poisk: '   ' }), 'search=соус').toString()).toBe('')
  expect(prochitat('search=%20').poisk).toBe('')
  expect(prochitat('search=+++').poisk).toBe('')
  // Пробел в конце набранного — часть поиска: шеф набирает второе слово.
  expect(prochitat(zapisat(sostoyanie({ poisk: 'соус ' })).toString()).poisk).toBe('соус ')
})

test('ключ колонки `sort` или `search` — ошибка в описании колонок', () => {
  const sKlyuchom = (key: string): OpisanieKolonki<Blyudo>[] => [...K, { key, title: 'Занятый ключ' }]
  for (const key of ['sort', 'search']) {
    expect(() => prochitatAdres(new URLSearchParams(), sKlyuchom(key))).toThrow(key)
    expect(() => zapisatAdres(new URLSearchParams(), PUSTOE, sKlyuchom(key))).toThrow(key)
  }
})
