/**
 * Поиск по уже загруженному списку — в браузере, без запроса к серверу.
 *
 * Без учёта регистра и «ё» = «е»: в справочнике «Ёлка», а повар пишет
 * «елка» — и наоборот. Букву «ё» на телефонной клавиатуре ещё надо найти.
 */

/**
 * Нижний регистр по-русски и «ё» → «е».
 *
 * Сначала NFC: в тексте из PDF или с macOS «ё» бывает записана двумя
 * знаками — «е» и комбинируемые две точки. NFC склеивает их в одну «ё»,
 * и только тогда её видит замена на «е».
 */
export function normalizovat(s: string): string {
  return s.normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё', 'е')
}

/**
 * Есть ли запрос в каком-нибудь из полей (название, id) как подстрока.
 * Пустой запрос — и одни пробелы — находит всё.
 */
export function sovpadaet(polya: readonly string[], zapros: string): boolean {
  const z = normalizovat(zapros.trim())
  if (z === '') return true
  return polya.some((pole) => normalizovat(pole).includes(z))
}
