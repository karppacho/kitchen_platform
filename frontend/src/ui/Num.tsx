import './num.css'

const NERAZRYVNYY = ' '

/**
 * Число из API — в вид, привычный шефу.
 *
 * Значения приходят строками, чтобы не потерять копейки на округлении
 * float. Здесь они строками и обрабатываются: ни одного parseFloat.
 */
export function formatNumber(value: string, fraction?: number): string {
  const minus = value.startsWith('-')
  const [tselaya = '0', drobnaya = ''] = value.replace('-', '').split('.')
  const znaki =
    fraction === undefined
      ? drobnaya.replace(/0+$/, '')
      : drobnaya.padEnd(fraction, '0').slice(0, fraction)
  const gruppy = tselaya.replace(/\B(?=(\d{3})+(?!\d))/g, NERAZRYVNYY)
  return `${minus ? '−' : ''}${gruppy}${znaki ? `,${znaki}` : ''}`
}

type Props = {
  value: string | null
  unit?: string
  fraction?: number
}

export function Num({ value, unit, fraction }: Props) {
  if (value === null || value === '') {
    // Прочерк приглушён и тоньше числа: он обязан быть отличим от нуля с
    // одного взгляда, иначе «маржи нет» и «маржа ноль» сливаются.
    return (
      <span className="num num--pusto" title="значения нет">
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
