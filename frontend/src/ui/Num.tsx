import './num.css'

// Неразрывный пробел U+00A0 (не обычный пробел U+0020)
const NERAZRYVNYY = ' '

/**
 * Число из API — в вид, привычный шефу.
 *
 * Значения приходят строками, чтобы не потерять копейки на округлении
 * float. Здесь они строками и обрабатываются: ни одного parseFloat.
 */
export function formatNumber(value: string, fraction?: number): string {
  const minus = value.startsWith('-')
  let [tselaya = '0', drobnaya = ''] = value.replace('-', '').split('.')

  let znaki: string
  if (fraction === undefined) {
    znaki = drobnaya.replace(/0+$/, '')
  } else {
    // Округление половины вверх
    if (drobnaya.length > fraction) {
      const lastDigit = Number(drobnaya[fraction] ?? '0')
      if (lastDigit >= 5) {
        // Нужно округлить вверх
        const digits = drobnaya.slice(0, fraction).split('')
        let carry = 1
        for (let i = digits.length - 1; i >= 0 && carry; i--) {
          const digit = Number(digits[i]!) + carry
          if (digit === 10) {
            digits[i] = '0'
            carry = 1
          } else {
            digits[i] = digit.toString()
            carry = 0
          }
        }
        drobnaya = digits.join('')
        if (carry) {
          // Перенос в целую часть
          const tselyeDigits = tselaya.split('')
          for (let i = tselyeDigits.length - 1; i >= 0 && carry; i--) {
            const digit = Number(tselyeDigits[i]!) + carry
            if (digit === 10) {
              tselyeDigits[i] = '0'
              carry = 1
            } else {
              tselyeDigits[i] = digit.toString()
              carry = 0
            }
          }
          if (carry) {
            tselaya = '1' + tselyeDigits.join('')
          } else {
            tselaya = tselyeDigits.join('')
          }
        }
      }
    }
    znaki = drobnaya.padEnd(fraction, '0').slice(0, fraction)
  }

  const gruppy = tselaya.replace(/\B(?=(\d{3})+(?!\d))/g, NERAZRYVNYY)

  // Проверка что это не отрицательный ноль
  const isZero = tselaya === '0' && (!znaki || znaki.replace(/0/g, '') === '')

  return `${!isZero && minus ? '−' : ''}${gruppy}${znaki ? `,${znaki}` : ''}`
}

type Props = {
  value: string | null
  unit?: string
  fraction?: number
}

export function Num({ value, unit, fraction }: Props) {
  if (value === null || value === '') {
    // Прочерк приглушён и отличается от нуля по цвету. Он обязан быть
    // отличим от нуля с одного взгляда, иначе «маржи нет» и «маржа ноль»
    // сливаются.
    return (
      <span className="num num--pusto" aria-label="значения нет">
        —
      </span>
    )
  }
  return (
    <span className="num">
      {formatNumber(value, fraction)}
      {unit ? NERAZRYVNYY + unit : ''}
    </span>
  )
}