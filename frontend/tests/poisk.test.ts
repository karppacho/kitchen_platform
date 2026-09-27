import { expect, test } from 'vitest'

import { normalizovat, sovpadaet } from '../src/domain/poisk'

// Поиск идёт в браузере по уже загруженному списку: по названию и id, без
// учёта регистра, «ё» = «е» — повар пишет «елка», а в справочнике «Ёлка».

const sous = ['Соус томатный', '1042']

test('находит по части названия без учёта регистра', () => {
  expect(sovpadaet(sous, 'ТОМАТ')).toBe(true)
  expect(sovpadaet(sous, 'сыр')).toBe(false)
})

test('находит по id', () => {
  expect(sovpadaet(sous, '104')).toBe(true)
})

test('«ЁЛКА» находится по «елка» и наоборот', () => {
  expect(normalizovat('ЁЛКА')).toBe('елка')
  expect(sovpadaet(['ЁЛКА'], 'елка')).toBe(true)
  expect(sovpadaet(['елка'], 'Ёлка')).toBe(true)
})

test('пустой запрос находит всё', () => {
  // Одни пробелы — тоже пустой запрос: случайный пробел в поле не должен
  // прятать весь список.
  expect(sovpadaet(sous, '')).toBe(true)
  expect(sovpadaet(sous, '   ')).toBe(true)
})
