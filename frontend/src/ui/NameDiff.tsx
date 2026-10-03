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
 * Что есть только в `a` — помечено (`<mark>`). Чего в `a` нет, а в `b`
 * есть, — вставлено на своё место зачёркнутым (`<del>`, «нет в
 * справочнике»): иначе пропущенное «не» не видно вовсе — все слова
 * кандидата есть и в карточке. Похожие слова («резанные» — «резаные»)
 * сравниваются по буквам; заменённые буквы показаны только со стороны
 * `a`, чужие в имя не вставляются. Непохожие слова — целиком: буквы,
 * случайно совпавшие у «моцареллы» и «пармезана», ничего не значат.
 */
export function NameDiff({ a, b }: { a: string; b: string }) {
  return (
    <span>
      {kuski(a, b).map((kusok, nomer) => {
        if (kusok.vid === 'obshchee') return kusok.tekst
        if (kusok.vid === 'lishnee') {
          return (
            <mark key={nomer} className="razlichie">
              {kusok.tekst}
            </mark>
          )
        }
        return (
          <del key={nomer} className="razlichie-net">
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

/** Слово — буквы и цифры подряд; между словами — пробелы и знаки. */
const SLOVO = /^[\p{L}\p{N}]/u
const TOKENY = /[\p{L}\p{N}]+|[^\p{L}\p{N}]+/gu

/**
 * Имя `a` по кускам. Имена режутся на слова и промежутки между ними и
 * выравниваются, как в diff: совпавшие (без учёта регистра) — общие,
 * похожие слова и промежутки — по буквам, остальное — лишнее или
 * пропущенное. Имена короткие, таблица выравнивания — сотни клеток.
 */
function kuski(a: string, b: string): Kusok[] {
  const ta = a.match(TOKENY) ?? []
  const tb = b.match(TOKENY) ?? []
  // ochki[i][j] — очки лучшего выравнивания хвостов ta[i:] и tb[j:].
  const ochki = Array.from({ length: ta.length + 1 }, () =>
    new Array<number>(tb.length + 1).fill(0),
  )
  for (let i = ta.length - 1; i >= 0; i -= 1) {
    for (let j = tb.length - 1; j >= 0; j -= 1) {
      const para = ves(ta[i]!, tb[j]!)
      ochki[i]![j] = Math.max(
        para === null ? 0 : para + ochki[i + 1]![j + 1]!,
        ochki[i + 1]![j]!,
        ochki[i]![j + 1]!,
      )
    }
  }

  const rezultat: Kusok[] = []
  let i = 0
  let j = 0
  while (i < ta.length || j < tb.length) {
    const x = ta[i]
    const y = tb[j]
    const para = x !== undefined && y !== undefined ? ves(x, y) : null
    if (para !== null && para + ochki[i + 1]![j + 1]! === ochki[i]![j]!) {
      rezultat.push(...poBukvam(x!, y!))
      i += 1
      j += 1
    } else if (x !== undefined && (y === undefined || ochki[i + 1]![j]! === ochki[i]![j]!)) {
      rezultat.push({ vid: 'lishnee', tekst: x })
      i += 1
    } else {
      rezultat.push({ vid: 'net', tekst: y! })
      j += 1
    }
  }
  // Пропуск подряд — одна вставка: «, не» — одно зачёркнутое, а не два.
  return skleit(skleit(rezultat).flatMap(propusk))
}

/**
 * Чего стоит поставить кусок `x` против `y`; `null` — ставить нельзя.
 * Совпавший — его длина. Похожие — половина общих краёв: совпадение
 * всегда дороже похожести. Слово против промежутка не ставится, слово
 * против слова — только если общие края хотя бы в половину длинного.
 */
function ves(x: string, y: string): number | null {
  if (x.toLowerCase() === y.toLowerCase()) return x.length
  const slovo = SLOVO.test(x)
  if (slovo !== SLOVO.test(y)) return null
  const { nachalo, konets } = kraya(x, y)
  const obshchee = nachalo + konets
  if (slovo && obshchee * 2 < Math.max(x.length, y.length)) return null
  return obshchee / 2
}

/** Похожие куски — по буквам: общие края, середина. Середина `a` —
 *  лишнее; середина `b` показывается, только когда у `a` её нет вовсе
 *  (вставка, а не замена). */
function poBukvam(x: string, y: string): Kusok[] {
  const { nachalo, konets } = kraya(x, y)
  const seredinaA = x.slice(nachalo, x.length - konets)
  const seredinaB = y.slice(nachalo, y.length - konets)
  return [
    { vid: 'obshchee', tekst: x.slice(0, nachalo) },
    seredinaA ? { vid: 'lishnee', tekst: seredinaA } : { vid: 'net', tekst: seredinaB },
    { vid: 'obshchee', tekst: x.slice(x.length - konets) },
  ]
}

/** Пропуск без пробелов по краям: пробелы не пропущены, они отделяют
 *  вставленное слово от соседей. Одни пробелы не показываются вовсе.
 *  Прочие куски — как есть. */
function propusk(kusok: Kusok): Kusok[] {
  if (kusok.vid !== 'net') return [kusok]
  const [, pered, sut, posle] = /^(\s*)(.*?)(\s*)$/su.exec(kusok.tekst)!
  if (!sut) return []
  return [
    { vid: 'obshchee', tekst: pered! },
    { vid: 'net', tekst: sut },
    { vid: 'obshchee', tekst: posle! },
  ]
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

/** Общее начало и общий конец без учёта регистра; конец не залезает
 *  на начало. Регистр снимается у каждой буквы отдельно: у целой строки
 *  длина после этого может измениться, и края съехали бы. */
function kraya(x: string, y: string): { nachalo: number; konets: number } {
  const ravny = (i: number, j: number) => x[i]!.toLowerCase() === y[j]!.toLowerCase()
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
