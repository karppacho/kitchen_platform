import { expect, test } from 'vitest'

import {
  podhodit,
  primenit,
  sobratGruppy,
  type GruppaFiltra,
  type OpisanieKolonki,
  type SostoyanieTablitsy,
  type Vybor,
} from '../src/domain/tablitsa'

// Фильтры как в Google-таблицах: внутри колонки — ИЛИ, между колонками — И;
// варианты и счётчики колонки — по строкам, прошедшим поиск и фильтры
// других колонок.

type Blyudo = { id: string; name: string; category: string; status: string; price: string | null }

const BLYUDA: Blyudo[] = [
  { id: 'B001', name: 'Соус сырный', category: 'Соус', status: 'активное', price: null },
  { id: 'B002', name: 'Пицца Маргарита', category: 'Пицца', status: 'активное', price: '450.00' },
  { id: 'B003', name: 'Лимонад', category: '', status: 'активное', price: '150.00' },
  { id: 'B004', name: 'Пицца Грибная', category: 'Пицца', status: 'архив', price: '0.00' },
  { id: 'B005', name: 'Соус томатный', category: 'Соус', status: 'архив', price: '90.00' },
]
const VSE = ['B001', 'B002', 'B003', 'B004', 'B005']

const KOLONKI: OpisanieKolonki<Blyudo>[] = [
  { key: 'id', title: 'ID', sort: { vid: 'tekst', znachenie: (r) => r.id } },
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.name } },
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
]

const ids = (stroki: readonly Blyudo[]) => stroki.map((r) => r.id)
const otobrat = (vybor: Vybor) => ids(BLYUDA.filter((r) => podhodit(r, KOLONKI, vybor)))
const gruppa = (gruppy: GruppaFiltra[], kolonka: string) => gruppy.find((g) => g.kolonka === kolonka)
const poiskPo = (r: Blyudo) => [r.name, r.id]
const sostoyanie = (s: Partial<SostoyanieTablitsy>): SostoyanieTablitsy => ({
  poisk: '',
  sortirovka: null,
  vybor: {},
  ...s,
})

test('без выбора проходит всё', () => {
  expect(otobrat({})).toEqual(VSE)
  expect(otobrat({ category: [], status: [] })).toEqual(VSE)
  // У названия фильтра нет — выбор по нему ничего не отбирает.
  expect(otobrat({ name: ['Лимонад'] })).toEqual(VSE)
})

test('внутри колонки — ИЛИ, между колонками — И', () => {
  expect(otobrat({ category: ['Пицца', 'Соус'] })).toEqual(['B001', 'B002', 'B004', 'B005'])
  expect(otobrat({ category: ['Пицца', 'Соус'], status: ['архив'] })).toEqual(['B004', 'B005'])
  // Готовые условия — так же: «есть» или «нет» вместе — все строки.
  expect(otobrat({ price: ['est', 'net'] })).toEqual(VSE)
  expect(otobrat({ category: ['Соус'], price: ['net'] })).toEqual(['B001'])
  // Пустое значение выбирается кодом '' — это «(пусто)».
  expect(otobrat({ category: [''] })).toEqual(['B003'])
})

test('группы — у колонок с фильтром; значения по алфавиту, «(пусто)» последним', () => {
  const gruppy = sobratGruppy(BLYUDA, KOLONKI, {})
  expect(gruppy.map((g) => g.kolonka)).toEqual(['category', 'status', 'price'])
  expect(gruppa(gruppy, 'category')).toEqual({
    kolonka: 'category',
    zagolovok: 'Категория',
    aktivna: false,
    varianty: [
      { kod: 'Пицца', podpis: 'Пицца', schyot: 2, vybran: false },
      { kod: 'Соус', podpis: 'Соус', schyot: 2, vybran: false },
      { kod: '', podpis: '(пусто)', schyot: 1, vybran: false },
    ],
  })
})

test('«Соус» и «соус» в списке галочек — в одном порядке, как бы ни шли строки', () => {
  // Для Collator они равны, а для фильтра — разные значения. Без второго
  // ключа их порядок зависел бы от порядка строк в ответе сервера.
  const sKategoriey = (category: string): Blyudo => ({ id: 'B001', name: 'Соус', category, status: '', price: null })
  const poryadok = (kategorii: string[]) =>
    gruppa(sobratGruppy(kategorii.map(sKategoriey), KOLONKI, {}), 'category')?.varianty.map((v) => v.kod)
  expect(poryadok(['соус', '', 'Соус'])).toEqual(['Соус', 'соус', ''])
  expect(poryadok(['Соус', '', 'соус'])).toEqual(['Соус', 'соус', ''])
})

test('счётчики группы не зависят от её выбора, но учитывают чужие группы и поиск', () => {
  const gruppy = sobratGruppy(BLYUDA, KOLONKI, { category: ['Пицца'] })
  // Свой выбор не сужает свой список: иначе к «Пицце» не добавить «Соус».
  expect(gruppa(gruppy, 'category')).toEqual({
    kolonka: 'category',
    zagolovok: 'Категория',
    aktivna: true,
    varianty: [
      { kod: 'Пицца', podpis: 'Пицца', schyot: 2, vybran: true },
      { kod: 'Соус', podpis: 'Соус', schyot: 2, vybran: false },
      { kod: '', podpis: '(пусто)', schyot: 1, vybran: false },
    ],
  })
  // Чужой выбор сужает: статусы считаются только по пиццам.
  expect(gruppa(gruppy, 'status')?.varianty).toEqual([
    { kod: 'активное', podpis: 'активное', schyot: 1, vybran: false },
    { kod: 'архив', podpis: 'архив', schyot: 1, vybran: false },
  ])

  // Поиск — тоже чужой фильтр.
  const poSousam = primenit(BLYUDA, KOLONKI, sostoyanie({ poisk: 'соус' }), poiskPo).gruppy
  expect(gruppa(poSousam, 'category')?.varianty).toEqual([
    { kod: 'Соус', podpis: 'Соус', schyot: 2, vybran: false },
  ])
  expect(gruppa(poSousam, 'price')?.varianty).toEqual([
    { kod: 'est', podpis: 'есть', schyot: 1, vybran: false },
    { kod: 'net', podpis: 'нет', schyot: 1, vybran: false },
  ])
})

test('выбранное с нулём строк видно и отмечено, невыбранное с нулём — нет', () => {
  const { stroki, gruppy } = primenit(
    BLYUDA,
    KOLONKI,
    sostoyanie({ poisk: 'пицца', vybor: { category: ['Соус'] } }),
    poiskPo,
  )
  expect(stroki).toEqual([])
  // «Соус» остаётся, чтобы с него можно было снять галочку; «(пусто)» не
  // выбрано и строк у него нет — его не видно.
  expect(gruppa(gruppy, 'category')?.varianty).toEqual([
    { kod: 'Пицца', podpis: 'Пицца', schyot: 2, vybran: false },
    { kod: 'Соус', podpis: 'Соус', schyot: 0, vybran: true },
  ])

  // Значение из старой ссылки, которого нет в данных, — тоже.
  const status = gruppa(sobratGruppy(BLYUDA, KOLONKI, { status: ['архивный'] }), 'status')
  expect(status?.varianty).toEqual([
    { kod: 'активное', podpis: 'активное', schyot: 3, vybran: false },
    { kod: 'архив', podpis: 'архив', schyot: 2, vybran: false },
    { kod: 'архивный', podpis: 'архивный', schyot: 0, vybran: true },
  ])
})

test('готовые условия видны всегда, даже с нулём строк', () => {
  const { gruppy } = primenit(BLYUDA, KOLONKI, sostoyanie({ poisk: 'лимонад' }), poiskPo)
  expect(gruppa(gruppy, 'price')).toEqual({
    kolonka: 'price',
    zagolovok: 'Цена меню',
    aktivna: false,
    varianty: [
      { kod: 'est', podpis: 'есть', schyot: 1, vybran: false },
      { kod: 'net', podpis: 'нет', schyot: 0, vybran: false },
    ],
  })
})

test('primenit: поиск и фильтры, затем сортировка; vsego — длина входа', () => {
  // «а» есть в названиях всех блюд, кроме «Соуса сырного».
  const itog = primenit(
    BLYUDA,
    KOLONKI,
    sostoyanie({
      poisk: 'а',
      sortirovka: { kolonka: 'price', napravlenie: 'ubyv' },
      vybor: { category: ['Пицца', 'Соус'] },
    }),
    poiskPo,
  )
  expect(ids(itog.stroki)).toEqual(['B002', 'B005', 'B004'])
  expect(itog.vsego).toBe(5)

  // Без сортировки и по колонке, которую не сортируют, — порядок ответа.
  expect(ids(primenit(BLYUDA, KOLONKI, sostoyanie({}), poiskPo).stroki)).toEqual(VSE)
  const poKategorii = sostoyanie({ sortirovka: { kolonka: 'category', napravlenie: 'vozr' } })
  expect(ids(primenit(BLYUDA, KOLONKI, poKategorii, poiskPo).stroki)).toEqual(VSE)
})
