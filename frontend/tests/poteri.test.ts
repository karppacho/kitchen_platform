import { expect, test } from 'vitest'

import { podskazkaPoteri, podskazkaSummy, summaPoter } from '../src/domain/poteri'

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

test('подсказка у поля потерь — правило и текст сервера: от 0 до 99,99 %', () => {
  expect(podskazkaPoteri('')).toBeNull()
  expect(podskazkaPoteri('0')).toBeNull()
  expect(podskazkaPoteri('99,99')).toBeNull()
  expect(podskazkaPoteri('12.5%')).toBeNull()
  expect(podskazkaPoteri('100')).toBe('от 0 до 99,99 %')
  expect(podskazkaPoteri('100,0')).toBe('от 0 до 99,99 %')
  expect(podskazkaPoteri('-1')).toBe('от 0 до 99,99 %')
  expect(podskazkaPoteri('пять')).toBe('введите число, например 5 или 12,5')
})

test('сумма потерь 100 % и больше — подсказка у итога, ниже — нет', () => {
  const VNE = 'Сумма потерь 100 % и больше — себестоимость станет нулевой, проверьте'
  expect(podskazkaSummy('99.99')).toBeNull()
  expect(podskazkaSummy('0')).toBeNull()
  expect(podskazkaSummy('100')).toBe(VNE)
  expect(podskazkaSummy('150.5')).toBe(VNE)
  // Итога нет — какое-то поле не число: подсказывает уже поле.
  expect(podskazkaSummy(null)).toBeNull()
})
