/**
 * Посимвольная подсветка различий двух имён.
 *
 * Различие и есть предмет решения: «огурцы маринованные не резаные» против
 * «Огурцы маринованные резанные» — похожесть 95 %, смысл противоположный.
 * Показать имена рядом без подсветки значит спрятать «не» в середине
 * строки.
 *
 * Сравнение идёт с краёв: общее начало, общий конец, различие посередине.
 * Полноценный diff здесь избыточен — имена короткие и отличаются одним
 * куском.
 */
export function NameDiff({ a, b }: { a: string; b: string }) {
  const nachalo = obshcheeNachalo(a, b)
  const konets = obshchiyKonets(a.slice(nachalo), b.slice(nachalo))

  const seredina = a.slice(nachalo, a.length - konets)

  return (
    <span>
      {a.slice(0, nachalo)}
      {seredina && <mark className="razlichie">{seredina}</mark>}
      {konets > 0 ? a.slice(a.length - konets) : ''}
    </span>
  )
}

function obshcheeNachalo(a: string, b: string): number {
  let i = 0
  while (i < a.length && i < b.length && a[i]!.toLowerCase() === b[i]!.toLowerCase()) i += 1
  return i
}

function obshchiyKonets(a: string, b: string): number {
  let i = 0
  while (
    i < a.length &&
    i < b.length &&
    a[a.length - 1 - i]!.toLowerCase() === b[b.length - 1 - i]!.toLowerCase()
  ) {
    i += 1
  }
  return i
}
