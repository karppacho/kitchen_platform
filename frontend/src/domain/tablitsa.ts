/**
 * Сортировка и фильтры таблиц — по правилам Google-таблиц.
 *
 * Шеф годами работал в Google-таблице, и её поведение для него — норма:
 * пустые ячейки при сортировке всегда внизу; внутри колонки отмеченные
 * значения складываются (ИЛИ), между колонками — сужают друг друга (И);
 * список значений колонки со счётчиками строится по строкам, прошедшим
 * фильтры других колонок, но не её собственный. Другое поведение он
 * принял бы за ошибку в данных.
 *
 * Всё считается в браузере по полному списку, поэтому здесь нет ни React,
 * ни запросов: экраны описывают колонки, а правила живут в одном месте.
 */

import { sovpadaet } from './poisk'
import { razobratDesyatichnoe, sravnitDesyatichnye, sravnitTekst } from './sravnenie'

export type Napravlenie = 'vozr' | 'ubyv'
export type Sortirovka = { kolonka: string; napravlenie: Napravlenie }
/** `podpisi` — пункты «по возрастанию / по убыванию» на узком экране. */
export type SposobSortirovki<T> =
  | { vid: 'tekst'; znachenie: (r: T) => string | null; podpisi?: readonly [string, string] }
  | { vid: 'chislo'; znachenie: (r: T) => string | number | null; podpisi?: readonly [string, string] }
/** Готовое условие фильтра: «есть», «нет», «выше цены меню». */
export type Uslovie<T> = { kod: string; podpis: string; podhodit: (r: T) => boolean }
export type SposobFiltra<T> =
  | { vid: 'znacheniya'; znachenie: (r: T) => string }
  | { vid: 'usloviya'; usloviya: readonly Uslovie<T>[] }
export type OpisanieKolonki<T> = { key: string; title: string; sort?: SposobSortirovki<T>; filtr?: SposobFiltra<T> }
/** Отмеченные коды по ключу колонки; пустой список — фильтра нет. */
export type Vybor = Readonly<Record<string, readonly string[]>>
export type SostoyanieTablitsy = { poisk: string; sortirovka: Sortirovka | null; vybor: Vybor }
export type Variant = { kod: string; podpis: string; schyot: number; vybran: boolean }
export type GruppaFiltra = { kolonka: string; zagolovok: string; varianty: Variant[]; aktivna: boolean }

/**
 * Готовые условия «есть / нет»: у цены и веса штуки это null против
 * значения (цена «0.00» — есть: прочерк и ноль значат разное), у карточки —
 * флаг. Одна пара на все экраны: коды `est`/`net` — они же в адресе
 * (`price=net`), и разойтись между экранами им нельзя.
 */
export function estNet<T>(est: (r: T) => boolean): SposobFiltra<T> {
  return {
    vid: 'usloviya',
    usloviya: [
      { kod: 'est', podpis: 'есть', podhodit: est },
      { kod: 'net', podpis: 'нет', podhodit: (r) => !est(r) },
    ],
  }
}

const PODPISI_TEKSTA = ['от А до Я', 'от Я до А'] as const
const PODPISI_CHISLA = ['по возрастанию', 'по убыванию'] as const
// Пустое значение фильтруется кодом '' — так же оно и пишется в адрес
// (`category=`), а людям показывается этой подписью.
const PUSTO = '(пусто)'

/**
 * Новый массив; исходный не трогается — это порядок ответа сервера, к нему
 * возвращает третий щелчок по заголовку.
 */
export function sortirovat<T>(stroki: readonly T[], s: SposobSortirovki<T>, n: Napravlenie): T[] {
  if (s.vid === 'tekst') return poKlyuchu(stroki, (r) => tekstIliPusto(s.znachenie(r)), sravnitTekst, n)
  return poKlyuchu(stroki, (r) => chisloIliPusto(s.znachenie(r)), sravnitChisla, n)
}

/**
 * Ключ считается один раз на строку, а не при каждом сравнении. `null` —
 * пустое: оно в конце в обе стороны, направление его не касается.
 *
 * «По убыванию» — обращённым сравнением, а не `reverse()`: переворот
 * переставил бы и равные строки, и пустые, а сортировка стабильна — равные
 * идут в порядке ответа сервера в любую сторону.
 */
function poKlyuchu<T, K>(
  stroki: readonly T[],
  klyuch: (r: T) => K | null,
  sravnit: (a: K, b: K) => number,
  n: Napravlenie,
): T[] {
  const pary = stroki.map((r) => ({ r, k: klyuch(r) }))
  pary.sort((a, b) => {
    if (a.k === null || b.k === null) {
      if (a.k === b.k) return 0
      return a.k === null ? 1 : -1
    }
    return n === 'vozr' ? sravnit(a.k, b.k) : sravnit(b.k, a.k)
  })
  return pary.map((p) => p.r)
}

// Строка из одних пробелов на экране выглядит пустой — и сортируется как пустая.
function tekstIliPusto(v: string | null): string | null {
  return v === null || v.trim() === '' ? null : v
}

/**
 * Пустое — `null`, `''` и строка, которая не десятичная запись: ставить её
 * между числами значило бы угадывать. NaN и бесконечность JSON не передаёт
 * вовсе — это сбой расчёта, не значение.
 */
function chisloIliPusto(v: string | number | null): string | number | null {
  if (v === null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  return razobratDesyatichnoe(v) === null ? null : v
}

/**
 * Числа (число замечаний) — как числа; строки (деньги, проценты) — как
 * десятичную запись, без перевода в float.
 */
function sravnitChisla(a: string | number, b: string | number): number {
  if (typeof a === 'number' && typeof b === 'number') return a < b ? -1 : a > b ? 1 : 0
  return sravnitDesyatichnye(desyatichnaya(a), desyatichnaya(b))
}

// Только для колонки, где числа и строки вперемешку (на деле такой нет).
// Целые — через BigInt: String(1e21) дал бы «1e+21», а это не десятичная
// запись. Дробных такого размера не бывает: все числа от 2^53 — целые.
// Дробь меньше 1e-6 String() тоже пишет с «e» — она стала бы «неразбираемой».
function desyatichnaya(x: string | number): string {
  if (typeof x === 'string') return x
  return Number.isInteger(x) ? BigInt(x).toString() : String(x)
}

/**
 * Проходит ли строка фильтры всех колонок, кроме `krome`: внутри колонки
 * — ИЛИ, между колонками — И. `krome` нужен для счётчиков: колонка
 * считает свои варианты без собственного выбора.
 */
export function podhodit<T>(
  r: T,
  kolonki: readonly OpisanieKolonki<T>[],
  vybor: Vybor,
  krome?: string,
): boolean {
  return kolonki.every((k) => {
    const f = k.filtr
    const vybrano = vybor[k.key] ?? []
    if (f === undefined || k.key === krome || vybrano.length === 0) return true
    if (f.vid === 'znacheniya') return vybrano.includes(f.znachenie(r))
    return f.usloviya.some((u) => vybrano.includes(u.kod) && u.podhodit(r))
  })
}

/**
 * Группы галочек — по одной на колонку с фильтром, в порядке колонок.
 *
 * Варианты колонки считаются по строкам, прошедшим поиск и фильтры других
 * колонок: собственный выбор свой список не сужает, иначе после первой же
 * галочки остальные значения колонки пропали бы и добавить их было бы
 * нельзя.
 */
export function sobratGruppy<T>(
  poslePoiska: readonly T[],
  kolonki: readonly OpisanieKolonki<T>[],
  vybor: Vybor,
): GruppaFiltra[] {
  return kolonki.flatMap((k) => {
    const f = k.filtr
    if (f === undefined) return []
    const vybrano = vybor[k.key] ?? []
    const stroki = poslePoiska.filter((r) => podhodit(r, kolonki, vybor, k.key))
    const varianty =
      f.vid === 'znacheniya'
        ? variantyZnacheniy(stroki, f.znachenie, vybrano)
        : f.usloviya.map((u) => ({
            kod: u.kod,
            podpis: u.podpis,
            // Условия видны всегда, даже с нулём: их немного, и набор не
            // должен прыгать от поиска.
            schyot: stroki.filter((r) => u.podhodit(r)).length,
            vybran: vybrano.includes(u.kod),
          }))
    return [{ kolonka: k.key, zagolovok: k.title, varianty, aktivna: vybrano.length > 0 }]
  })
}

/**
 * Значения, которые есть в строках, — по алфавиту, «(пусто)» последним.
 * Выбранное значение, у которого сейчас 0 строк, остаётся видимым и
 * отмеченным — иначе с него не снять галочку (оно могло прийти и из старой
 * ссылки). Невыбранное с нулём не показывается.
 */
function variantyZnacheniy<T>(
  stroki: readonly T[],
  znachenie: (r: T) => string,
  vybrano: readonly string[],
): Variant[] {
  const schyot = new Map<string, number>()
  for (const r of stroki) {
    const kod = znachenie(r)
    schyot.set(kod, (schyot.get(kod) ?? 0) + 1)
  }
  for (const kod of vybrano) {
    if (!schyot.has(kod)) schyot.set(kod, 0)
  }
  const varianty = [...schyot].map(([kod, n]) => ({
    kod,
    podpis: kod === '' ? PUSTO : kod,
    schyot: n,
    vybran: vybrano.includes(kod),
  }))
  return varianty.sort((a, b) => sravnitZnacheniyaFiltra(a.kod, b.kod))
}

/**
 * Порядок значений фильтра — в списке галочек и в адресе: по алфавиту,
 * «(пусто)» последним. «Соус» и «соус» для Collator равны, а для фильтра —
 * разные значения; без второго ключа их порядок зависел бы от порядка строк
 * в ответе или отметок в адресе.
 */
export function sravnitZnacheniyaFiltra(a: string, b: string): number {
  if ((a === '') !== (b === '')) return a === '' ? 1 : -1
  return sravnitTekst(a, b) || (a < b ? -1 : a > b ? 1 : 0)
}

/**
 * Поиск, затем фильтры, затем сортировка — сортируются только оставшиеся
 * строки. `vsego` — длина входа: «Найдено: N из M».
 */
export function primenit<T>(
  stroki: readonly T[],
  kolonki: readonly OpisanieKolonki<T>[],
  s: SostoyanieTablitsy,
  poiskPo: (r: T) => readonly string[],
): { stroki: T[]; gruppy: GruppaFiltra[]; vsego: number } {
  const poslePoiska = stroki.filter((r) => sovpadaet(poiskPo(r), s.poisk))
  const gruppy = sobratGruppy(poslePoiska, kolonki, s.vybor)
  const otobrannye = poslePoiska.filter((r) => podhodit(r, kolonki, s.vybor))
  const sortirovka = s.sortirovka
  // Колонка без сортировки или неизвестная — порядок ответа сервера.
  const sposob = sortirovka && kolonki.find((k) => k.key === sortirovka.kolonka)?.sort
  return {
    stroki: sortirovka && sposob ? sortirovat(otobrannye, sposob, sortirovka.napravlenie) : otobrannye,
    gruppy,
    vsego: stroki.length,
  }
}

/**
 * Пункты выбора «Сортировка» на узком экране — по два на колонку:
 * код `key` — по возрастанию, `-key` — по убыванию (как в адресе).
 */
export function variantySortirovki<T>(kolonki: readonly OpisanieKolonki<T>[]): { kod: string; podpis: string }[] {
  return kolonki.flatMap((k) => {
    if (k.sort === undefined) return []
    const [vozr, ubyv] = k.sort.podpisi ?? (k.sort.vid === 'tekst' ? PODPISI_TEKSTA : PODPISI_CHISLA)
    return [
      { kod: k.key, podpis: `${k.title}: ${vozr}` },
      { kod: `-${k.key}`, podpis: `${k.title}: ${ubyv}` },
    ]
  })
}
