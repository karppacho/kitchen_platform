/**
 * Потери в форме переноса в справочник — как их вводит человек: в
 * процентах, «12,5», «5 %», пусто — ноль.
 *
 * Здесь только подсказка и итог «для сверки»: решает сервер (422 у поля).
 * Арифметика — над десятичной записью, без float: 0,1 + 0,2 во float дают
 * 0,30000000000000004, и итог соврал бы шефу. Целые цифры складываются
 * через BigInt — он точен.
 */

import { razobratDesyatichnoe, sravnitDesyatichnye, type Desyatichnoe } from './sravnenie'

/** Текст подсказки — тот же смысл, что у отказа сервера: 100 % значит, что
 *  от продукта ничего не остаётся, и расчёт делил бы на ноль. */
export const VNE_PREDELA = 'от 0 до 100 %, меньше 100'
export const NE_CHISLO = 'не число'

/**
 * Введённое — в десятичную запись: без разрядных пробелов и знака
 * процента, запятая — точка. Пусто — ноль, как у сервера. `null` — не
 * число.
 */
function razobratVvod(vvod: string): Desyatichnoe | null {
  const chisto = vvod.replace(/\s/g, '').replace(/%$/, '').replace(',', '.')
  if (chisto === '') return { minus: false, tsel: '0', drob: '' }
  return razobratDesyatichnoe(chisto)
}

function vZapis({ minus, tsel, drob }: Desyatichnoe): string {
  return `${minus ? '-' : ''}${tsel}${drob === '' ? '' : `.${drob}`}`
}

/** Подсказка у поля потерь; `null` — всё в порядке. */
export function podskazkaPoteri(vvod: string): string | null {
  const chislo = razobratVvod(vvod)
  if (chislo === null) return NE_CHISLO
  if (chislo.minus || sravnitDesyatichnye(vZapis(chislo), '100') >= 0) return VNE_PREDELA
  return null
}

/**
 * Общие потери — сумма трёх полей, десятичной записью («17.5»). `null` —
 * какое-то поле не число или с минусом: итог из него был бы неправдой.
 */
export function summaPoter(vvody: readonly string[]): string | null {
  const chisla: Desyatichnoe[] = []
  for (const vvod of vvody) {
    const chislo = razobratVvod(vvod)
    if (chislo === null || chislo.minus) return null
    chisla.push(chislo)
  }
  const znakov = Math.max(0, ...chisla.map((c) => c.drob.length))
  let summa = 0n
  for (const { tsel, drob } of chisla) summa += BigInt(tsel + drob.padEnd(znakov, '0'))
  const tsifry = summa.toString().padStart(znakov + 1, '0')
  const tsel = tsifry.slice(0, tsifry.length - znakov)
  const drob = tsifry.slice(tsifry.length - znakov).replace(/0+$/, '')
  return vZapis({ minus: false, tsel, drob })
}
