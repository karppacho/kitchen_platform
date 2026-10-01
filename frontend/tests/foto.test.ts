import { afterEach, beforeEach, expect, test } from 'vitest'

import { FotoNeOtkrylos, razmerPosleUmensheniya, umenshitFoto } from '../src/domain/foto'
import { heic, podmenitBrauzer, snimok } from './podmenaFoto'

let brauzer: ReturnType<typeof podmenitBrauzer>

beforeEach(() => {
  brauzer = podmenitBrauzer()
})
afterEach(() => brauzer.vernut())

test('снимок камеры 4032×3024 уменьшается до 1600×1200 и уходит JPEG', async () => {
  expect(razmerPosleUmensheniya(4032, 3024)).toEqual({ shirina: 1600, vysota: 1200 })

  const { foto, prevyu } = await umenshitFoto(snimok(4032, 3024))

  expect(foto.type).toBe('image/jpeg')
  expect(await foto.text()).toBe('jpeg 1600x1200')
  expect(brauzer.risovanie).toHaveBeenCalledWith(expect.any(HTMLImageElement), 0, 0, 1600, 1200)
  expect(brauzer.kachestva).toEqual([0.85])
  // Превью — адресом data: — его пропускает политика безопасности страницы.
  expect(prevyu).toMatch(/^data:image\/jpeg;base64,/)
})

test('портретный снимок — уменьшается по длинной стороне, вертикальной', async () => {
  expect(razmerPosleUmensheniya(3024, 4032)).toEqual({ shirina: 1200, vysota: 1600 })

  const { foto } = await umenshitFoto(snimok(3024, 4032))

  expect(await foto.text()).toBe('jpeg 1200x1600')
})

test('маленькое фото не трогается — не растягивается', async () => {
  expect(razmerPosleUmensheniya(800, 600)).toEqual({ shirina: 800, vysota: 600 })
  expect(razmerPosleUmensheniya(1600, 900)).toEqual({ shirina: 1600, vysota: 900 })

  const { foto } = await umenshitFoto(snimok(800, 600))

  // Размер прежний; JPEG — всё равно: скриншот бывает PNG, а сервер берёт
  // только JPEG.
  expect(await foto.text()).toBe('jpeg 800x600')
  expect(foto.type).toBe('image/jpeg')
})

test('доли пикселя округляются, а сторона не сжимается в ноль', () => {
  expect(razmerPosleUmensheniya(4000, 3001)).toEqual({ shirina: 1600, vysota: 1200 })
  expect(razmerPosleUmensheniya(20000, 5)).toEqual({ shirina: 1600, vysota: 1 })
})

test('фото не открылось — понятный текст про формат', async () => {
  const otkaz = umenshitFoto(heic())

  await expect(otkaz).rejects.toBeInstanceOf(FotoNeOtkrylos)
  await expect(otkaz).rejects.toThrow(/HEIC/)
  // Что сделать на iPhone, чтобы камера снимала в JPEG.
  await expect(otkaz).rejects.toThrow(/«Наиболее совместимый»/)
  expect(brauzer.risovanie).not.toHaveBeenCalled()
})
