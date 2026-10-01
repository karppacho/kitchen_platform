import { expect, test } from 'vitest'

import { zamechaniyaSlovami } from '../src/domain/slova'

test('склонение «замечание»', () => {
  expect(zamechaniyaSlovami(0)).toBeNull()
  expect(zamechaniyaSlovami(1)).toBe('1 замечание')
  expect(zamechaniyaSlovami(2)).toBe('2 замечания')
  expect(zamechaniyaSlovami(4)).toBe('4 замечания')
  expect(zamechaniyaSlovami(5)).toBe('5 замечаний')
  expect(zamechaniyaSlovami(11)).toBe('11 замечаний')
  expect(zamechaniyaSlovami(21)).toBe('21 замечание')
  expect(zamechaniyaSlovami(112)).toBe('112 замечаний')
})
