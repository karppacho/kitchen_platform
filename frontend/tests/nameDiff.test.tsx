import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { NameDiff } from '../src/ui/NameDiff'

// <NameDiff> — единственное место, где человек видит, чем один вариант
// отличается от другого. Различие и есть предмет решения («не резаные»
// против «резанные», похожесть 95 %), поэтому тест обязан доказывать, что
// помечены именно и только различающиеся символы — не факт, что где-то
// на странице есть <mark>.

/** Что видит глаз: без текста, скрытого для программы чтения экрана. */
function vidno(uzel: Element): string {
  const kopiya = uzel.cloneNode(true) as Element
  kopiya.querySelectorAll('.vizualno-skryto').forEach((skrytoe) => skrytoe.remove())
  return kopiya.textContent ?? ''
}

function pometki(container: HTMLElement) {
  return {
    podsvecheno: [...container.querySelectorAll('mark')].map(vidno),
    zacherknuto: [...container.querySelectorAll('del')].map(vidno),
    imya: vidno(container.querySelector('span')!),
  }
}

test('различающийся хвост помечен, совпадающее начало — нет', () => {
  const { container } = render(<NameDiff a="привет" b="привед" />)

  const otmetki = container.querySelectorAll('mark')
  expect(otmetki).toHaveLength(1)
  // Различается только последняя буква: «т» у a против «д» у b. Замена
  // показана со стороны a — «д» не вставляется в чужое имя.
  expect(otmetki[0]!.textContent).toBe('т')
  expect(container.querySelector('del')).toBeNull()

  // Совпадающее начало «приве» — обычный текстовый узел, а не внутри
  // <mark>: если бы разметка помечала всё подряд, эта проверка бы упала.
  const span = container.querySelector('span')!
  expect(span.firstChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(span.firstChild?.textContent).toBe('приве')
  expect(span.textContent).toBe('привет')
})

test('пропущенное слово видно: «не» из карточки — как отсутствующее в справочнике', () => {
  // Живая проверка 02.10: карточка «Огурцы маринованные, не резанные»,
  // кандидат из справочника «Огурцы маринованные резанные». Все слова
  // кандидата есть в карточке — без показа пропуска пометок не было бы
  // вовсе, а смысл противоположный. Запятая — не различие: не зачёркнута.
  const { container } = render(
    <NameDiff a="Огурцы маринованные резанные" b="Огурцы маринованные, не резанные" />,
  )

  expect(pometki(container)).toEqual({
    podsvecheno: [],
    zacherknuto: ['не'],
    imya: 'Огурцы маринованные не резанные',
  })
  // Зачёркивание программа чтения экрана не произносит — сказано словами.
  expect(container.querySelector('del')).toHaveTextContent('нет в справочнике: не')
})

test('лишнее слово в имени справочника подсвечено, запятая перед ним — нет', () => {
  // Та же пара в обратную сторону: «не» есть у кандидата, нет у карточки.
  const { container } = render(
    <NameDiff a="Огурцы маринованные, не резанные" b="Огурцы маринованные резанные" />,
  )

  expect(pometki(container)).toEqual({
    podsvecheno: ['не'],
    zacherknuto: [],
    imya: 'Огурцы маринованные, не резанные',
  })
})

test('у пометок есть подсказка при наведении: чего где нет', () => {
  const { container } = render(<NameDiff a="Сыр моцарелла" b="Сыр пармезан" />)
  expect(container.querySelector('mark')).toHaveAttribute('title', 'нет в карточке')
  expect(container.querySelector('del')).toHaveAttribute('title', 'нет в справочнике')
})

test('слово вставлено в середину — края не помечены, вставка на своём месте', () => {
  const { container } = render(<NameDiff a="Корж для пиццы" b="Корж для римской пиццы" />)

  expect(pometki(container)).toEqual({
    podsvecheno: [],
    zacherknuto: ['римской'],
    imya: 'Корж для римской пиццы',
  })
})

test('живой пример: пропущенное «не» и лишняя «н» — каждая своей пометкой', () => {
  // Карточка «Огурцы маринованные, не резаные» против кандидата «Огурцы
  // маринованные резанные»: в справочнике нет «не», а в слове справочника
  // лишняя «н». Запятая карточки не показывается вовсе.
  const { container } = render(
    <NameDiff a="Огурцы маринованные резанные" b="Огурцы маринованные, не резаные" />,
  )

  expect(pometki(container)).toEqual({
    podsvecheno: ['н'],
    zacherknuto: ['не'],
    imya: 'Огурцы маринованные не резанные',
  })
  // Общий хвост «ые» идёт после <mark> обычным текстом, не внутри него:
  // помечена одна «н» из двух, а не всё слово.
  const span = container.querySelector('span')!
  expect(span.lastChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(span.lastChild?.textContent).toBe('ые')
})

test('внутри похожего слова недостающая буква не зачёркивается', () => {
  // Внутри слова — только подсветка изменённых букв справочника: чужая
  // буква посреди слова читается хуже, чем помогает.
  const { container } = render(<NameDiff a="Томат" b="Томаты" />)
  expect(pometki(container)).toEqual({ podsvecheno: [], zacherknuto: [], imya: 'Томат' })
})

test.each([
  ['Сыр моцарелла', 'Сыр пармезан', 'моцарелла', 'пармезан', 'Сыр моцарелла пармезан'],
  ['Сливки 10%', 'Сливки 33%', '10', '33', 'Сливки 10 33%'],
])(
  'совсем разные слова на одном месте — две пометки через пробел: %s / %s',
  (a, b, svoyo, chuzhoe, imya) => {
    // Похожие слова сравниваются по буквам, непохожие — целиком: буквы,
    // случайно совпавшие у «моцареллы» и «пармезана», ничего не значат.
    // Вплотную «моцарелла» и «пармезан» читались бы одним словом, «10» и
    // «33» — числом «1033».
    const { container } = render(<NameDiff a={a} b={b} />)
    expect(pometki(container)).toEqual({ podsvecheno: [svoyo], zacherknuto: [chuzhoe], imya })
  },
)

test('одинаковые имена — совсем без пометки', () => {
  render(<NameDiff a="Сахар" b="Сахар" />)
  expect(screen.getByText('Сахар')).toBeInTheDocument()
  expect(document.querySelector('mark')).toBeNull()
  expect(document.querySelector('del')).toBeNull()
})

test.each([
  ['Соус Сырный', 'соус сырный'],
  ['Огурцы маринованные, резанные', 'огурцы  маринованные резанные.'],
  ['Свёкла отварная', 'свекла отварная'],
  [' Сахар ', 'Сахар'],
  ['Оснвова для пиццы круглая , неаполитанская.', 'Оснвова для пиццы круглая, неаполитанская'],
])('точные тёзки по меркам сервера — без пометок: «%s» / «%s»', (a, b) => {
  // Сервер сводит имена без регистра, «ё» к «е», знаки и лишние пробелы
  // убирает — для него это одно имя. Пометка здесь соврала бы о различии.
  const { container } = render(<NameDiff a={a} b={b} />)
  expect(container.querySelector('mark')).toBeNull()
  expect(container.querySelector('del')).toBeNull()
  expect(container.textContent).toBe(a)
})
