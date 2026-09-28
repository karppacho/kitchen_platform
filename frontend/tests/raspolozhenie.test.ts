import { expect, test } from 'vitest'

import { raspolozhit, SHIRINA_PANELI } from '../src/ui/raspolozhenie'

// Панель фильтра — в портале, в координатах документа: якорь (значок в
// шапке) меряется в координатах окна, прокрутка прибавляется после.

const BEZ_PROKRUTKI = { x: 0, y: 0 }
// Значок 14 px, низ шапки — на 40 px от верха окна.
const ZNACHOK = { left: 500, right: 514, bottom: 40 }

test('ширина панели — 264', () => {
  expect(SHIRINA_PANELI).toBe(264)
})

test('колонка влево — панель начинается под левым краем значка', () => {
  expect(raspolozhit(ZNACHOK, 'left', 1440, BEZ_PROKRUTKI)).toEqual({ left: 500, top: 44 })
})

test('колонка вправо — панель кончается под правым краем значка', () => {
  // Числа прижаты к правому краю колонки, значок — тоже; панель уходит
  // влево, над колонкой, а не за край таблицы.
  expect(raspolozhit(ZNACHOK, 'right', 1440, BEZ_PROKRUTKI)).toEqual({ left: 514 - 264, top: 44 })
})

test('у правого края окна панель прижата к нему с отступом', () => {
  const uKraya = { left: 1400, right: 1414, bottom: 40 }
  expect(raspolozhit(uKraya, 'left', 1440, BEZ_PROKRUTKI)).toEqual({ left: 1440 - 264 - 8, top: 44 })
})

test('у левого края окна панель прижата к нему с отступом', () => {
  const uKraya = { left: 20, right: 34, bottom: 40 }
  expect(raspolozhit(uKraya, 'right', 1440, BEZ_PROKRUTKI)).toEqual({ left: 8, top: 44 })
})

test('прокрутка страницы прибавляется к координатам', () => {
  expect(raspolozhit(ZNACHOK, 'left', 1440, { x: 30, y: 600 })).toEqual({ left: 530, top: 644 })
})

test('прижим — по краям окна, прокрутка — после', () => {
  // Страница прокручена вправо на 30: прижатая панель стоит у правого края
  // окна, то есть в документе на 30 правее, а не в 30 px от края документа.
  const uKraya = { left: 1400, right: 1414, bottom: 40 }
  expect(raspolozhit(uKraya, 'left', 1440, { x: 30, y: 600 })).toEqual({ left: 1440 - 264 - 8 + 30, top: 644 })
})
