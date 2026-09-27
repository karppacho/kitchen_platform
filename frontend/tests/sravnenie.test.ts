import { expect, test } from 'vitest'

import { razobratDesyatichnoe, sravnitDesyatichnye, sravnitTekst } from '../src/domain/sravnenie'
import { dorozhe } from '../src/domain/tseny'

// Деньги и проценты приходят строками, а маржа бывает и отрицательной
// (−399,3): сортировать их надо как десятичную запись со знаком — не через
// float (запрещён) и не как строки.

test('числа со знаком идут по порядку, в том числе отрицательные', () => {
  // У отрицательных порядок модулей обратный: −399,3 меньше −10, хотя модуль
  // больше, а −1,5 меньше −1,25, хотя дробная часть «5» короче «25».
  const poryadok = ['-399.3', '-10', '-1.5', '-1.25', '0', '9.50', '10.00', '100.00']
  poryadok.forEach((a, i) => {
    poryadok.forEach((b, j) => {
      expect(sravnitDesyatichnye(a, b), `${a} против ${b}`).toBe(Math.sign(i - j))
    })
  })
  const vperemeshku = ['10.00', '-1.25', '100.00', '-399.3', '0', '-1.5', '9.50', '-10']
  expect(vperemeshku.sort(sravnitDesyatichnye)).toEqual(poryadok)
})

test('одно и то же число в разной записи — равно', () => {
  // «-0» — не отрицательное число: иначе ноль с минусом встал бы перед нулём.
  const pary = [
    ['-0', '0'],
    ['0', '0.00'],
    ['-0', '0.00'],
    ['-0.00', '0'],
    ['007', '7'],
    ['1', '1.000'],
  ]
  for (const [a = '', b = ''] of pary) {
    expect(sravnitDesyatichnye(a, b), `${a} = ${b}`).toBe(0)
    expect(sravnitDesyatichnye(b, a), `${b} = ${a}`).toBe(0)
  }
})

test('разбор приводит запись к одному виду', () => {
  expect(razobratDesyatichnoe('-007.50')).toEqual({ minus: true, tsel: '7', drob: '5' })
  expect(razobratDesyatichnoe('000')).toEqual({ minus: false, tsel: '0', drob: '' })
  expect(razobratDesyatichnoe('-0.00')).toEqual({ minus: false, tsel: '0', drob: '' })
})

test('не десятичная запись — null', () => {
  // Сервер пишет числа только так: необязательный минус, цифры, точка, цифры.
  // Всё прочее — не число, и угадывать его значение нельзя.
  for (const musor of ['', 'abc', '1e5', '1.2.3', '+5']) {
    expect(razobratDesyatichnoe(musor), JSON.stringify(musor)).toBeNull()
  }
})

test('неразбираемое больше любого числа', () => {
  // При сортировке мусор уходит в конец, а не встаёт между числами.
  expect(sravnitDesyatichnye('abc', '100.00')).toBe(1)
  expect(sravnitDesyatichnye('-399.3', '')).toBe(-1)
})

test('неразбираемая себестоимость или цена — не «дороже»', () => {
  // Сравнение ставит неразбираемое выше любого числа, но «выше цены меню»
  // красит строку тревогой — по мусору красить нельзя. tests/tseny.test.ts
  // не трогаем: он доказывает, что замена сравнения ничего не сломала.
  expect(dorozhe('abc', '10.00')).toBe(false)
  expect(dorozhe('10.00', 'abc')).toBe(false)
})

test('текст: числа внутри сравниваются по значению', () => {
  // id «12» раньше «123», а «123» раньше «1000», хотя как строки «1000» < «123».
  expect(sravnitTekst('12', '123')).toBeLessThan(0)
  expect(sravnitTekst('123', '1000')).toBeLessThan(0)
})

test('текст: «ё» и регистр не различаются', () => {
  expect(sravnitTekst('ёж', 'еж')).toBe(0)
  expect(sravnitTekst('Ж', 'ж')).toBe(0)
})
