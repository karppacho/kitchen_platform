/**
 * Сравнение значений для сортировки таблиц.
 *
 * Деньги и проценты приходят с бэкенда строками и строками остаются: перевод
 * в float теряет копейки (см. общие ограничения проекта), а наивное
 * сравнение строк ошибается, где длины целых частей расходятся
 * (`"9.50" > "10.00"` как строки). К тому же маржа бывает отрицательной
 * (−399,3), и у отрицательных порядок модулей обратный. Поэтому число
 * сначала разбирается на знак, целую и дробную части, а сравнивается по ним.
 */

export type Desyatichnoe = { minus: boolean; tsel: string; drob: string }

// Только так сервер пишет числа. «+5», «1e5», «.5» — не его запись: угадывать
// их значение значит молча сортировать мусор среди чисел.
const DESYATICHNOE = /^(-?)(\d+)(?:\.(\d+))?$/

/**
 * Знак, целая часть без ведущих нулей (`''` → `'0'`) и дробная без хвостовых;
 * `null`, если строка — не десятичная запись.
 *
 * Приведение к одному виду делает сравнение простым: `007` и `7`, `1` и
 * `1.000` дают одинаковые части. `-0` — не отрицательное число, иначе ноль с
 * минусом встал бы перед нулём.
 */
export function razobratDesyatichnoe(s: string): Desyatichnoe | null {
  const chasti = DESYATICHNOE.exec(s)
  if (chasti === null) return null
  const tsel = (chasti[2] ?? '').replace(/^0+/, '') || '0'
  const drob = (chasti[3] ?? '').replace(/0+$/, '')
  const nol = tsel === '0' && drob === ''
  return { minus: chasti[1] === '-' && !nol, tsel, drob }
}

/**
 * −1, 0 или 1. Знак → длина целой части → целая → дробная, дополненная
 * нулями до общей длины; у отрицательных порядок модулей обращён.
 *
 * Неразбираемое больше любого числа: при сортировке по возрастанию оно
 * уходит в конец, а не встаёт между числами.
 */
export function sravnitDesyatichnye(a: string, b: string): -1 | 0 | 1 {
  const x = razobratDesyatichnoe(a)
  const y = razobratDesyatichnoe(b)
  if (x === null || y === null) {
    if (x === y) return 0
    return x === null ? 1 : -1
  }
  if (x.minus !== y.minus) return x.minus ? -1 : 1
  const moduli = sravnitModuli(x, y)
  return x.minus ? obratit(moduli) : moduli
}

function sravnitModuli(x: Desyatichnoe, y: Desyatichnoe): -1 | 0 | 1 {
  // Ведущих нулей нет, поэтому более длинная целая часть — большее число.
  if (x.tsel.length !== y.tsel.length) return x.tsel.length < y.tsel.length ? -1 : 1
  if (x.tsel !== y.tsel) return x.tsel < y.tsel ? -1 : 1
  const dlina = Math.max(x.drob.length, y.drob.length)
  const xd = x.drob.padEnd(dlina, '0')
  const yd = y.drob.padEnd(dlina, '0')
  if (xd === yd) return 0
  return xd < yd ? -1 : 1
}

// Не `-r`: из нуля вышел бы `-0`, а он не равен `0` для Object.is.
function obratit(r: -1 | 0 | 1): -1 | 0 | 1 {
  return r === 0 ? 0 : r === 1 ? -1 : 1
}

// Один на модуль: создание Collator дорого, а сортировка зовёт сравнение
// тысячи раз. numeric — id «12» раньше «123», а «123» раньше «1000»;
// sensitivity 'base' — регистр и «ё»/«е» не различаются.
const TEKST = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' })

/** Сравнение текста по-русски: для сортировки по названию, категории, id. */
export function sravnitTekst(a: string, b: string): number {
  return TEKST.compare(a, b)
}
