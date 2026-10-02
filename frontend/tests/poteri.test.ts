import { expect, test } from 'vitest'

import { podskazkaPoteri, summaPoter } from '../src/domain/poteri'

test('итог потерь — точная десятичная сумма, без двоичных дробей', () => {
  // 0,1 + 0,2 во float — 0,30000000000000004: итог «для сверки» соврал бы.
  expect(summaPoter(['0.1', '0,2', ''])).toBe('0.3')
  expect(summaPoter(['5', '12,5', '0'])).toBe('17.5')
  // Пустое поле — ноль, как у сервера.
  expect(summaPoter(['', '', ''])).toBe('0')
  // Так пишут люди: разрядный пробел, знак процента.
  expect(summaPoter([' 1 030 ', '0', '0'])).toBe('1030')
  expect(summaPoter(['5%', '0,25', '0,75'])).toBe('6')
})

test('не число или минус — итога нет, а не неправда', () => {
  expect(summaPoter(['пять', '0', '0'])).toBeNull()
  expect(summaPoter(['-5', '10', '0'])).toBeNull()
})

test('подсказка у поля потерь — правило сервера: от 0 и меньше 100', () => {
  expect(podskazkaPoteri('')).toBeNull()
  expect(podskazkaPoteri('0')).toBeNull()
  expect(podskazkaPoteri('99,99')).toBeNull()
  expect(podskazkaPoteri('12.5%')).toBeNull()
  expect(podskazkaPoteri('100')).toBe('от 0 до 100 %, меньше 100')
  expect(podskazkaPoteri('100,0')).toBe('от 0 до 100 %, меньше 100')
  expect(podskazkaPoteri('-1')).toBe('от 0 до 100 %, меньше 100')
  expect(podskazkaPoteri('пять')).toBe('не число')
})
