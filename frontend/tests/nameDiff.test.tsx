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
  // кандидат из справочника «Огурцы маринованные резанные». Всё имя
  // кандидата совпадает с кусками карточки — без показа пропуска пометок
  // не было бы вовсе, а смысл противоположный.
  const { container } = render(
    <NameDiff a="Огурцы маринованные резанные" b="Огурцы маринованные, не резанные" />,
  )

  expect(container.querySelector('mark')).toBeNull()
  const propuski = container.querySelectorAll('del')
  expect(propuski).toHaveLength(1)
  expect(vidno(propuski[0]!)).toBe(', не')
  // Пропуск стоит на своём месте, имя кандидата вокруг — обычным текстом.
  expect(vidno(container.querySelector('span')!)).toBe('Огурцы маринованные, не резанные')
  // Зачёркивание программа чтения экрана не произносит — сказано словами.
  expect(propuski[0]).toHaveTextContent('нет в справочнике: , не')
})

test('лишнее слово в имени справочника помечено, а не показано пропуском', () => {
  // Та же пара в обратную сторону: «не» есть у кандидата, нет у карточки.
  const { container } = render(
    <NameDiff a="Огурцы маринованные, не резанные" b="Огурцы маринованные резанные" />,
  )

  expect(container.querySelector('del')).toBeNull()
  const otmetki = container.querySelectorAll('mark')
  expect(otmetki).toHaveLength(1)
  expect(otmetki[0]!.textContent).toBe(', не')
  expect(container.querySelector('span')!.textContent).toBe('Огурцы маринованные, не резанные')
})

test('слово вставлено в середину — края не помечены, вставка на своём месте', () => {
  const { container } = render(<NameDiff a="Корж для пиццы" b="Корж для римской пиццы" />)

  expect(container.querySelector('mark')).toBeNull()
  const propuski = container.querySelectorAll('del')
  expect(propuski).toHaveLength(1)
  expect(vidno(propuski[0]!)).toBe('римской')
  expect(vidno(container.querySelector('span')!)).toBe('Корж для римской пиццы')
})

test('пропущенное слово и опечатка вместе: каждая — своей пометкой', () => {
  // «Огурцы маринованные резанные» — реальный кандидат из ТЗ против
  // «огурцы маринованные не резаные»: в справочнике нет «не», а в слове
  // справочника лишняя «н».
  const { container } = render(
    <NameDiff a="Огурцы маринованные резанные" b="огурцы маринованные не резаные" />,
  )

  const otmetki = container.querySelectorAll('mark')
  expect(otmetki).toHaveLength(1)
  expect(otmetki[0]!.textContent).toBe('н')
  const propuski = container.querySelectorAll('del')
  expect(propuski).toHaveLength(1)
  expect(vidno(propuski[0]!)).toBe('не')

  // Общий хвост «ые» идёт после <mark> обычным текстом, не внутри него:
  // помечена одна «н» из двух, а не всё слово.
  const span = container.querySelector('span')!
  expect(span.lastChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(span.lastChild?.textContent).toBe('ые')
  expect(vidno(span)).toBe('Огурцы маринованные не резанные')
})

test('буква, которой нет в слове справочника, тоже видна', () => {
  const { container } = render(<NameDiff a="Томат" b="Томаты" />)
  expect(container.querySelector('mark')).toBeNull()
  expect(vidno(container.querySelector('del')!)).toBe('ы')
})

test('совсем разные слова: своё помечено, чужое показано пропуском', () => {
  // Похожие слова сравниваются по буквам, непохожие — целиком: буквы
  // «моцареллы», случайно совпавшие с «пармезаном», ничего не значат.
  const { container } = render(<NameDiff a="Сыр моцарелла" b="Сыр пармезан" />)

  const otmetki = container.querySelectorAll('mark')
  expect(otmetki).toHaveLength(1)
  expect(otmetki[0]!.textContent).toBe('моцарелла')
  const propuski = container.querySelectorAll('del')
  expect(propuski).toHaveLength(1)
  expect(vidno(propuski[0]!)).toBe('пармезан')
  expect(container.querySelector('span')!.firstChild?.textContent).toBe('Сыр ')
})

test('одинаковые имена — совсем без пометки', () => {
  render(<NameDiff a="Сахар" b="Сахар" />)
  expect(screen.getByText('Сахар')).toBeInTheDocument()
  expect(document.querySelector('mark')).toBeNull()
  expect(document.querySelector('del')).toBeNull()
})

test('регистр — не различие', () => {
  const { container } = render(<NameDiff a="Соус Сырный" b="соус сырный" />)
  expect(container.querySelector('mark')).toBeNull()
  expect(container.querySelector('del')).toBeNull()
  expect(container.textContent).toBe('Соус Сырный')
})
