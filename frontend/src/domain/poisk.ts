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

function bezLishnihProbelov(s: string): string {
  return s.trim().replace(/\s+/g, ' ')
}

/**
 * Вариант из списка, если введённое отличается от него только регистром и
 * пробелами: «  метро » → «Метро». Иначе — введённое без лишних пробелов.
 *
 * Поставщик и категория уходят в лист как написаны: «метро» рядом с
 * «Метро» стало бы вторым поставщиком. «ё» и «е» здесь разные — это уже
 * другое написание, и выбирать его за повара незачем.
 */
export function kakVSpiske(vvedeno: string, spisok: readonly string[]): string {
  const chisto = bezLishnihProbelov(vvedeno)
  const klyuch = chisto.toLocaleLowerCase('ru')
  return spisok.find((v) => bezLishnihProbelov(v).toLocaleLowerCase('ru') === klyuch) ?? chisto
}
