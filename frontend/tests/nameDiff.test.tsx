import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { NameDiff } from '../src/ui/NameDiff'

// <NameDiff> — единственное место, где человек видит, чем один вариант
// отличается от другого. Различие и есть предмет решения («не резаные»
// против «резанные», похожесть 95 %), поэтому тест обязан доказывать, что
// помечены именно и только различающиеся символы — не факт, что где-то
// на странице есть <mark>.
test('различающийся хвост помечен, совпадающее начало — нет', () => {
  const { container } = render(<NameDiff a="привет" b="привед" />)

  const otmetki = container.querySelectorAll('mark')
  expect(otmetki).toHaveLength(1)
  // Различается только последняя буква: «т» у a против «д» у b.
  expect(otmetki[0]!.textContent).toBe('т')

  // Совпадающее начало «приве» — обычный текстовый узел, а не внутри
  // <mark>: если бы разметка помечала всё подряд, эта проверка бы упала.
  const span = container.querySelector('span')!
  expect(span.firstChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(span.firstChild?.textContent).toBe('приве')
  expect(span.textContent).toBe('привет')
})

test('различие может быть в середине: общие края не помечены', () => {
  // «Огурцы маринованные резанные» — реальный кандидат из ТЗ. Различие с
  // «не резаные» — не в конце и не в начале строки.
  const { container } = render(
    <NameDiff a="Огурцы маринованные резанные" b="огурцы маринованные не резаные" />,
  )
  const mark = container.querySelector('mark')
  expect(mark).not.toBeNull()
  expect(mark!.textContent).toBe('резан')

  // Общий хвост «ные» идёт после <mark> обычным текстом, не внутри него.
  const span = container.querySelector('span')!
  expect(span.lastChild?.nodeType).toBe(Node.TEXT_NODE)
  expect(span.lastChild?.textContent).toBe('ные')
})

test('одинаковые имена — совсем без пометки', () => {
  render(<NameDiff a="Сахар" b="Сахар" />)
  expect(screen.getByText('Сахар')).toBeInTheDocument()
  expect(document.querySelector('mark')).toBeNull()
})
