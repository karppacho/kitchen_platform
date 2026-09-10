import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { Num, formatNumber } from '../src/ui/Num'

describe('formatNumber', () => {
  test('разделитель дробной части — запятая', () => {
    expect(formatNumber('84.66', 2)).toBe('84,66')
  })

  test('тысячи разделяются неразрывным пробелом', () => {
    expect(formatNumber('1234.5', 1)).toBe('1 234,5')
  })

  test('без указания разрядности хвостовые нули убираются', () => {
    // Выход блюда приходит как "72.000" — показывать «72,000 г» незачем.
    expect(formatNumber('72.000')).toBe('72')
  })

  test('разрядность добивается нулями', () => {
    expect(formatNumber('100', 2)).toBe('100,00')
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
