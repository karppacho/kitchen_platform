import './nameDiff.css'

/**
 * Подсветка различий двух имён на «Сверке»: `a` — имя из справочника,
 * которое показываем, `b` — имя карточки, с которым сравниваем.
 *
 * Различие и есть предмет решения: «огурцы маринованные не резаные» против
 * «Огурцы маринованные резанные» — похожесть 95 %, смысл противоположный.
 * Показать имена рядом без подсветки значит спрятать «не» в середине
 * строки.
 *
 * Сравниваются слова — буквы и цифры подряд — так же, как сервер сводит
 * имена (`normalise_name` в `domain/matching.py`): без регистра, «ё» — это
 * «е», знаки и пробелы не в счёт. Поэтому у точных тёзок пометок нет.
 * - Слово есть только в `a` — подсвечено (`<mark>`, «нет в карточке»).
 * - Слова нет в `a`, а в `b` есть — вставлено на своё место подсвеченным и
 *   зачёркнутым (`<del>`, «нет в справочнике»): иначе пропущенное «не» не
 *   видно вовсе — все слова кандидата есть и в карточке.
 * - Похожие слова («резанные» — «резаные») — по буквам: подсвечены только
 *   изменённые буквы слова из справочника, чужие буквы не вставляются.
 * - Непохожие слова — целиком: буквы, случайно совпавшие у «моцареллы» и
 *   «пармезана», ничего не значат.
 *
 * Соседние пометки разделены пробелом: «моцарелла» и «пармезан» вплотную
 * читались бы одним словом.
 */
export function NameDiff({ a, b }: { a: string; b: string }) {
  return (
    <span>
      {kuski(a, b).map((kusok, nomer) => {
        if (kusok.vid === 'obshchee') return kusok.tekst
        if (kusok.vid === 'lishnee') {
          return (
            <mark key={nomer} className="razlichie" title="нет в карточке">
              {kusok.tekst}
            </mark>
          )
        }
        return (
          <del key={nomer} className="razlichie-net" title="нет в справочнике">
            {/* Зачёркивание программа чтения экрана не произносит. */}
            <span className="vizualno-skryto">нет в справочнике: </span>
            {kusok.tekst}
          </del>
        )
      })}
    </span>
  )
}

/** Кусок показанного имени: общее, есть только в `a`, есть только в `b`. */
type Kusok = { vid: 'obshchee' | 'lishnee' | 'net'; tekst: string }

/** Что стало со словом `a`: есть и в `b`, есть похожее, нет вовсе. */
type Sudba = { vid: 'tochno' } | { vid: 'pohozhe'; s: string } | { vid: 'lishnee' }

/** Буквы и цифры — как `\w` у сервера; прочее — знаки и пробелы. */
const SLOVA = /[\p{L}\p{M}\p{N}_]+/gu
const TOKENY = /[\p{L}\p{M}\p{N}_]+|[^\p{L}\p{M}\p{N}_]+/gu
const SLOVO = /^[\p{L}\p{M}\p{N}_]/u
const SLOVO_V_KONTSE = /[\p{L}\p{M}\p{N}_]$/u

/**
 * Имя `a` по кускам. Слова обоих имён выравниваются, как в diff: совпавшие
 * — общие, похожие — по буквам, остальные — лишние у `a` или пропущенные.
 * Знаки и пробелы `a` показываются как есть и не помечаются никогда;
 * пропуск — кусок `b` от первого пропущенного слова до последнего. Имена
 * короткие, таблица выравнивания — сотни клеток.
 */
function kuski(a: string, b: string): Kusok[] {
  const ta = a.match(TOKENY) ?? []
  const sa = ta.filter((token) => SLOVO.test(token))
  const sb = [...b.matchAll(SLOVA)].map((slovo) => ({
    tekst: slovo[0],
    nachalo: slovo.index,
    konets: slovo.index + slovo[0].length,
  }))

  // ochki[i][j] — очки лучшего выравнивания хвостов sa[i:] и sb[j:].
  const ochki = Array.from({ length: sa.length + 1 }, () =>
    new Array<number>(sb.length + 1).fill(0),
  )
  for (let i = sa.length - 1; i >= 0; i -= 1) {
    for (let j = sb.length - 1; j >= 0; j -= 1) {
      const para = ves(sa[i]!, sb[j]!.tekst)
      ochki[i]![j] = Math.max(
        para === null ? 0 : para + ochki[i + 1]![j + 1]!,
        ochki[i + 1]![j]!,
        ochki[i]![j + 1]!,
      )
    }
  }

  const sudby: Sudba[] = []
  // Пропуски по месту: после слова `a` с этим номером (-1 — перед первым).
  const propuski = new Map<number, { nachalo: number; konets: number }>()
  let i = 0
  let j = 0
  while (i < sa.length || j < sb.length) {
    const x = sa[i]
    const y = sb[j]
    const para = x !== undefined && y !== undefined ? ves(x, y.tekst) : null
    if (para !== null && para + ochki[i + 1]![j + 1]! === ochki[i]![j]!) {
      sudby.push(svesti(x!) === svesti(y!.tekst) ? { vid: 'tochno' } : { vid: 'pohozhe', s: y!.tekst })
      i += 1
      j += 1
    } else if (x !== undefined && (y === undefined || ochki[i + 1]![j]! === ochki[i]![j]!)) {
      sudby.push({ vid: 'lishnee' })
      i += 1
    } else {
      const bylo = propuski.get(i - 1)
      propuski.set(i - 1, { nachalo: bylo?.nachalo ?? y!.nachalo, konets: y!.konets })
      j += 1
    }
  }

  const vyhod: Kusok[] = []
  const vstavitPropusk = (posle: number) => {
    const propusk = propuski.get(posle)
    if (propusk) vyhod.push({ vid: 'net', tekst: b.slice(propusk.nachalo, propusk.konets) })
  }
  vstavitPropusk(-1)
  let nomer = 0
  for (const token of ta) {
    if (!SLOVO.test(token)) {
      vyhod.push({ vid: 'obshchee', tekst: token })
      continue
    }
    const sudba = sudby[nomer]!
    if (sudba.vid === 'tochno') vyhod.push({ vid: 'obshchee', tekst: token })
    else if (sudba.vid === 'lishnee') vyhod.push({ vid: 'lishnee', tekst: token })
    else vyhod.push(...poBukvam(token, sudba.s))
    vstavitPropusk(nomer)
    nomer += 1
  }
  return skleit(razdelit(skleit(vyhod)))
}

/**
 * Чего стоит поставить слово `x` против `y`; `null` — ставить нельзя.
 * Совпавшее — его длина. Похожие — половина общих краёв: совпадение
 * всегда дороже похожести. Непохожие — общие края меньше половины
 * длинного — не ставятся.
 */
function ves(x: string, y: string): number | null {
  if (svesti(x) === svesti(y)) return x.length
  const { nachalo, konets } = kraya(x, y)
  const obshchee = nachalo + konets
  if (obshchee * 2 < Math.max(x.length, y.length)) return null
  return obshchee / 2
}

/** Похожее слово по буквам: общие края, середина слова `a` подсвечена.
 *  Середина `b` не показывается: чужие буквы в слово не вставляются. */
function poBukvam(x: string, y: string): Kusok[] {
  const { nachalo, konets } = kraya(x, y)
  return [
    { vid: 'obshchee', tekst: x.slice(0, nachalo) },
    { vid: 'lishnee', tekst: x.slice(nachalo, x.length - konets) },
    { vid: 'obshchee', tekst: x.slice(x.length - konets) },
  ]
}

/** Пропуск и его сосед вплотную, оба на букве или цифре, — между ними
 *  пробел: иначе «моцарелла» и «пармезан» сливаются в одно слово. */
function razdelit(spisok: Kusok[]): Kusok[] {
  const rezultat: Kusok[] = []
  for (const kusok of spisok) {
    const pred = rezultat[rezultat.length - 1]
    if (
      pred &&
      (pred.vid === 'net' || kusok.vid === 'net') &&
      SLOVO_V_KONTSE.test(pred.tekst) &&
      SLOVO.test(kusok.tekst)
    ) {
      rezultat.push({ vid: 'obshchee', tekst: ' ' })
    }
    rezultat.push(kusok)
  }
  return rezultat
}

/** Соседние куски одного вида — один кусок; пустые выброшены. */
function skleit(spisok: Kusok[]): Kusok[] {
  const rezultat: Kusok[] = []
  for (const kusok of spisok) {
    if (!kusok.tekst) continue
    const posledniy = rezultat[rezultat.length - 1]
    if (posledniy?.vid === kusok.vid) posledniy.tekst += kusok.tekst
    else rezultat.push({ ...kusok })
  }
  return rezultat
}

/** Как сервер сводит имена: NFKC, без регистра, «ё» — это «е». */
function svesti(tekst: string): string {
  return tekst.normalize('NFKC').toLowerCase().replaceAll('ё', 'е')
}

/** Общее начало и общий конец двух слов, буквы сведены по одной; конец
 *  не залезает на начало. По одной — потому что у целой строки длина
 *  после сведения может измениться, и края съехали бы. */
function kraya(x: string, y: string): { nachalo: number; konets: number } {
  const ravny = (i: number, j: number) => svesti(x[i]!) === svesti(y[j]!)
  let nachalo = 0
  while (nachalo < x.length && nachalo < y.length && ravny(nachalo, nachalo)) nachalo += 1
  let konets = 0
  while (
    konets < x.length - nachalo &&
    konets < y.length - nachalo &&
    ravny(x.length - 1 - konets, y.length - 1 - konets)
  ) {
    konets += 1
  }
  return { nachalo, konets }
}
