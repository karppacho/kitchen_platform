import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { Num, formatNumber } from '../src/ui/Num'

describe('formatNumber', () => {
  test('разделитель дробной части — запятая', () => {
    expect(formatNumber('84.66', 2)).toBe('84,66')
  })

  test('тысячи разделяются неразрывным пробелом U+00A0', () => {
    // Неразрывный пробел U+00A0, а не обычный U+0020
    const result = formatNumber('1234.5', 1)
    expect(result).toBe('1 234,5')
    // Проверка что именно U+00A0
    expect(result.charCodeAt(1)).toBe(0x00A0)
  })

  test('без указания разрядности хвостовые нули убираются', () => {
    // Выход блюда приходит как "72.000" — показывать «72,000 г» незачем.
    expect(formatNumber('72.000')).toBe('72')
  })

  test('разрядность добивается нулями', () => {
    expect(formatNumber('100', 2)).toBe('100,00')
  })

  test('дробная часть округляется половины вверх', () => {
    expect(formatNumber('22.96', 1)).toBe('23,0')
  })

  test('округление с переносом разряда: 9.96 → 10,0', () => {
    expect(formatNumber('9.96', 1)).toBe('10,0')
  })

  test('округление с переносом разряда: 0.999 → 1,00', () => {
    expect(formatNumber('0.999', 2)).toBe('1,00')
  })

  test('отрицательный ноль не показывает минус', () => {
    expect(formatNumber('-0.001', 2)).toBe('0,00')
  })

  test('отрицательное число с округлением вверх к нулю теряет минус', () => {
    expect(formatNumber('-0.004', 2)).toBe('0,00')
  })

  test('перенос вместе с группировкой разрядов: 999.99 → 1 000,0', () => {
    // Граничный случай: перенос разряда удлиняет целую часть, вместе с этим
    // появляется новая группа из трёх разрядов, разделяемая неразрывным пробелом
    const result = formatNumber('999.99', 1)
    expect(result).toBe('1 000,0')
    // Проверка что разделитель тысяч — неразрывный пробел U+00A0
    expect(result.charCodeAt(1)).toBe(0x00A0)
  })

  test('перенос, удлиняющий целую часть: 99.99 → 100,0', () => {
    // Перенос разряда делает число трёхзначным
    expect(formatNumber('99.99', 1)).toBe('100,0')
  })

  test('отрицательное число с переносом: -22.96 → −23,0', () => {
    // Отрицательное число, при округлении переносит разряд, но знак сохраняется
    expect(formatNumber('-22.96', 1)).toBe('−23,0')
  })

  test('граница округления: 0.05 → 0,1', () => {
    // Значение ровно на половине (последняя цифра дробной части = 5)
    // Используется правило округления половины вверх
    expect(formatNumber('0.05', 1)).toBe('0,1')
  })
})

describe('Num', () => {
  test('null даёт прочерк, а не ноль', () => {
    // У 14 блюд из 130 нет цены меню. Прочерк значит «посчитать не из
    // чего»; ноль в той же колонке значил бы «маржа ровно ноль».
    render(<Num value={null} unit="₽" />)
    const znak = screen.getByText('—')
    expect(znak).toHaveClass('num--pusto')
    expect(screen.queryByText(/0/)).not.toBeInTheDocument()
  })

  test('ноль прочерком не притворяется', () => {
    render(<Num value="0" unit="%" />)
    expect(screen.getByText('0 %')).not.toHaveClass('num--pusto')
  })

  test('единица показывается явно', () => {
    render(<Num value="84.66" fraction={2} unit="₽" />)
    expect(screen.getByText('84,66 ₽')).toBeInTheDocument()
  })
})
