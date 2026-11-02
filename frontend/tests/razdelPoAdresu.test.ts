import { expect, test } from 'vitest'

import { RAZDELY } from '../src/shell/razdely'
import { razdelPoAdresu } from '../src/shell/razdelPoAdresu'

test('точный путь раздела', () => {
  expect(razdelPoAdresu('/dishes', RAZDELY)?.nazvanie).toBe('Блюда')
})

test('вложенный путь — карточка блюда — тоже «Блюда»', () => {
  expect(razdelPoAdresu('/dishes/B001', RAZDELY)?.nazvanie).toBe('Блюда')
})

test('похожий префикс без слэша — не раздел', () => {
  // «/dishesX» не должен считаться «Блюдами».
  expect(razdelPoAdresu('/dishesX', RAZDELY)).toBeNull()
})

test('неизвестный адрес — null', () => {
  expect(razdelPoAdresu('/net-takogo', RAZDELY)).toBeNull()
  expect(razdelPoAdresu('/', RAZDELY)).toBeNull()
})
