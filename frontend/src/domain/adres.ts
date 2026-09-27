/**
 * Состояние таблицы в адресе: `?search=соус&sort=-margin&category=Блюдо&uc=vyshe`.
 *
 * Ссылку на отобранный список шеф шлёт в переписке — по ней должен открыться
 * тот же вид. Поэтому адрес короткий и читаемый: ключи — `key` колонок,
 * значение по умолчанию не пишется, у колонки с несколькими отметками
 * параметр повторяется. Ссылки, присланные до этого (`?status=…&search=…`),
 * открываются так же: `status` — просто ключ колонки статуса.
 *
 * Адрес может прийти откуда угодно — с опечаткой, от старой версии, чужой
 * программы, — поэтому при чтении всё неизвестное отбрасывается: иначе
 * опечатка давала бы пустую таблицу без видимой причины. Свои параметры —
 * поиск, сортировка и ключи колонок с фильтром; остальные чужие, их запись
 * не трогает.
 */

import { sravnitTekst } from './sravnenie'
import type { OpisanieKolonki, SostoyanieTablitsy, SposobFiltra, Sortirovka } from './tablitsa'

export const PARAM_POISKA = 'search'
export const PARAM_SORTIROVKI = 'sort'

/** `margin` — по возрастанию, `-margin` — по убыванию. */
export function kodSortirovki(s: Sortirovka): string {
  return s.napravlenie === 'ubyv' ? `-${s.kolonka}` : s.kolonka
}

/** Разбор без проверки колонки; `''` и `'-'` — без сортировки. */
export function razobratKodSortirovki(kod: string): Sortirovka | null {
  const ubyv = kod.startsWith('-')
  const kolonka = ubyv ? kod.slice(1) : kod
  if (kolonka === '') return null
  return { kolonka, napravlenie: ubyv ? 'ubyv' : 'vozr' }
}

/**
 * Поиск из одних пробелов — пустой: он ничего не отбирает (`sovpadaet`
 * тоже так считает), значит, и в адрес не пишется, и активным не считается.
 * Непустой остаётся как набран: пробел в конце — это шеф начал второе
 * слово, отрезать его нельзя.
 */
export function poiskIliPusto(poisk: string): string {
  return poisk.trim() === '' ? '' : poisk
}

export function prochitatAdres<T>(p: URLSearchParams, kolonki: readonly OpisanieKolonki<T>[]): SostoyanieTablitsy {
  proveritKlyuchi(kolonki)
  const kod = p.get(PARAM_SORTIROVKI)
  const sortirovka = kod === null ? null : razobratKodSortirovki(kod)
  const vybor: Record<string, readonly string[]> = {}
  for (const k of kolonki) {
    if (k.filtr === undefined) continue
    const kody = dopustimye(k.filtr, p.getAll(k.key))
    if (kody.length > 0) vybor[k.key] = kody
  }
  return {
    poisk: poiskIliPusto(p.get(PARAM_POISKA) ?? ''),
    sortirovka: sortirovka && sortiruetsya(kolonki, sortirovka.kolonka) ? sortirovka : null,
    vybor,
  }
}

/**
 * Новые параметры: чужие из `prezhnie` — как были, в прежнем порядке;
 * за ними свои — поиск, сортировка, фильтры в порядке колонок. Порядок
 * зависит только от состояния, не от того, в каком порядке ставили
 * галочки: один и тот же вид — одна и та же ссылка.
 */
export function zapisatAdres<T>(
  prezhnie: URLSearchParams,
  s: SostoyanieTablitsy,
  kolonki: readonly OpisanieKolonki<T>[],
): URLSearchParams {
  proveritKlyuchi(kolonki)
  const svoi = new Set([PARAM_POISKA, PARAM_SORTIROVKI])
  for (const k of kolonki) if (k.filtr !== undefined) svoi.add(k.key)

  const novye = new URLSearchParams()
  for (const [klyuch, znachenie] of prezhnie) {
    if (!svoi.has(klyuch)) novye.append(klyuch, znachenie)
  }
  const poisk = poiskIliPusto(s.poisk)
  if (poisk !== '') novye.append(PARAM_POISKA, poisk)
  if (s.sortirovka && sortiruetsya(kolonki, s.sortirovka.kolonka)) {
    novye.append(PARAM_SORTIROVKI, kodSortirovki(s.sortirovka))
  }
  for (const k of kolonki) {
    if (k.filtr === undefined) continue
    for (const kod of dopustimye(k.filtr, s.vybor[k.key] ?? [])) novye.append(k.key, kod)
  }
  return novye
}

// Колонка с ключом `sort` или `search` делила бы параметр с сортировкой или
// поиском, и ссылка читалась бы двояко. Это ошибка в описании колонок, а не
// в адресе, — падаем сразу, на первом же открытии экрана.
function proveritKlyuchi<T>(kolonki: readonly OpisanieKolonki<T>[]): void {
  for (const k of kolonki) {
    if (k.key === PARAM_POISKA || k.key === PARAM_SORTIROVKI) {
      throw new Error(`Ключ колонки «${k.key}» занят адресом таблицы`)
    }
  }
}

function sortiruetsya<T>(kolonki: readonly OpisanieKolonki<T>[], kolonka: string): boolean {
  return kolonki.some((k) => k.key === kolonka && k.sort !== undefined)
}

/**
 * Коды выбора без повторов и в одном порядке. У условий — только известные
 * коды, в порядке объявления (`price=foo` отбрасывается). У списка значений
 * годится любое: значения приходят из ответа сервера, заранее их не знает
 * никто, — порядок как в списке галочек: по алфавиту, «(пусто)» последним.
 */
function dopustimye<T>(f: SposobFiltra<T>, kody: readonly string[]): string[] {
  if (f.vid === 'usloviya') return f.usloviya.map((u) => u.kod).filter((kod) => kody.includes(kod))
  return [...new Set(kody)].sort((a, b) => {
    if ((a === '') !== (b === '')) return a === '' ? 1 : -1
    // «Соус» и «соус» для Collator равны, а для фильтра — разные значения;
    // без второго ключа их порядок зависел бы от порядка отметок.
    return sravnitTekst(a, b) || (a < b ? -1 : a > b ? 1 : 0)
  })
}
