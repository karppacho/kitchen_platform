import { expect, test } from 'vitest'

import {
  sortirovat,
  variantySortirovki,
  type OpisanieKolonki,
  type SposobSortirovki,
} from '../src/domain/tablitsa'

// Сортировка как в Google-таблицах: пустое всегда в конце, в какую сторону
// ни сортируй; равные остаются в порядке ответа сервера.

const poChislu: SposobSortirovki<string | number | null> = { vid: 'chislo', znachenie: (v) => v }
const poTekstu: SposobSortirovki<string | null> = { vid: 'tekst', znachenie: (v) => v }

test('маржа: отрицательные по порядку, прочерк в конце в обе стороны', () => {
  const marzhi = ['77.1', null, '-399.3', '0']
  expect(sortirovat(marzhi, poChislu, 'vozr')).toEqual(['-399.3', '0', '77.1', null])
  expect(sortirovat(marzhi, poChislu, 'ubyv')).toEqual(['77.1', '0', '-399.3', null])
  // Новый массив: исходный — порядок ответа сервера, к нему возвращает
  // третий щелчок по заголовку.
  expect(marzhi).toEqual(['77.1', null, '-399.3', '0'])
})

test('текст: null, пустая строка и пробелы — в конце в обе стороны', () => {
  const imena = ['Соус', null, 'Анис', '', '  ', 'Мёд']
  expect(sortirovat(imena, poTekstu, 'vozr')).toEqual(['Анис', 'Мёд', 'Соус', null, '', '  '])
  expect(sortirovat(imena, poTekstu, 'ubyv')).toEqual(['Соус', 'Мёд', 'Анис', null, '', '  '])
})

test('равные ключи сохраняют порядок ответа в обе стороны', () => {
  // «По убыванию» — обращённым сравнением, а не reverse(): переворот
  // переставил бы и равные между собой строки.
  type R = { id: string; m: string }
  const stroki: R[] = [
    { id: 'a', m: '1' },
    { id: 'b', m: '2.0' },
    { id: 'c', m: '1.00' },
    { id: 'd', m: '2' },
  ]
  const poM: SposobSortirovki<R> = { vid: 'chislo', znachenie: (r) => r.m }
  expect(sortirovat(stroki, poM, 'vozr').map((r) => r.id)).toEqual(['a', 'c', 'b', 'd'])
  expect(sortirovat(stroki, poM, 'ubyv').map((r) => r.id)).toEqual(['b', 'd', 'a', 'c'])

  // Для текста «Ёж» и «еж» равны (регистр и «ё» не различаются).
  const ezhi = ['Ёж', 'Уж', 'еж']
  expect(sortirovat(ezhi, poTekstu, 'vozr')).toEqual(['Ёж', 'еж', 'Уж'])
  expect(sortirovat(ezhi, poTekstu, 'ubyv')).toEqual(['Уж', 'Ёж', 'еж'])
})

test('число замечаний сравнивается как число: 10 больше 2', () => {
  expect(sortirovat([2, 10, 0], poChislu, 'vozr')).toEqual([0, 2, 10])
  expect(sortirovat([2, 10, 0], poChislu, 'ubyv')).toEqual([10, 2, 0])
  // Без перевода в строку: String(1e21) — «1e+21», не десятичная запись, и
  // число стало бы «пустым».
  expect(sortirovat([1e21, 2], poChislu, 'ubyv')).toEqual([1e21, 2])
  // И со знаком: «-1e+21» ушло бы в «неразбираемые» — после всех чисел, а
  // не перед ними. Проверка выше этого не видит: «неразбираемое» и так
  // больше любого числа, как 1e21.
  expect(sortirovat([2, -1e21], poChislu, 'vozr')).toEqual([-1e21, 2])
  // Числа и строки вперемешку в одной колонке на деле не встречаются, но и
  // так порядок верный — в том числе у числа, которое String() пишет с «e».
  const vperemeshku = ['1000000000000000000001', 1e21, '5']
  expect(sortirovat(vperemeshku, poChislu, 'vozr')).toEqual(['5', 1e21, '1000000000000000000001'])
})

test('число: null, пустая строка, мусор и NaN — в конце в обе стороны', () => {
  const znacheniya = ['abc', 5, null, '', '-1', Number.NaN, '10.50']
  expect(sortirovat(znacheniya, poChislu, 'vozr')).toEqual(['-1', 5, '10.50', 'abc', null, '', Number.NaN])
  expect(sortirovat(znacheniya, poChislu, 'ubyv')).toEqual(['10.50', 5, '-1', 'abc', null, '', Number.NaN])
})

test('варианты сортировки: коды x и -x, подписи по умолчанию и свои', () => {
  type R = { name: string; warnings: number; margin: string | null }
  const kolonki: OpisanieKolonki<R>[] = [
    { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.name } },
    { key: 'photo', title: 'Фото' },
    { key: 'warnings', title: 'Замечания', sort: { vid: 'chislo', znachenie: (r) => r.warnings } },
    {
      key: 'margin',
      title: 'Маржа %',
      sort: { vid: 'chislo', znachenie: (r) => r.margin, podpisi: ['сначала худшая', 'сначала лучшая'] },
    },
  ]
  expect(variantySortirovki(kolonki)).toEqual([
    { kod: 'name', podpis: 'Название: от А до Я' },
    { kod: '-name', podpis: 'Название: от Я до А' },
    { kod: 'warnings', podpis: 'Замечания: по возрастанию' },
    { kod: '-warnings', podpis: 'Замечания: по убыванию' },
    { kod: 'margin', podpis: 'Маржа %: сначала худшая' },
    { kod: '-margin', podpis: 'Маржа %: сначала лучшая' },
  ])
})
