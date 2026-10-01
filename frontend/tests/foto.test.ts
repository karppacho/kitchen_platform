import { afterEach, beforeEach, expect, test } from 'vitest'

import {
  FotoNeOtkrylos,
  NeHvatiloPamyati,
  razmerPosleUmensheniya,
  umenshitFoto,
} from '../src/domain/foto'
import { heic, podmenitBrauzer, snimok, soderzhimoe } from './podmenaFoto'

let brauzer: ReturnType<typeof podmenitBrauzer>

beforeEach(() => {
  brauzer = podmenitBrauzer()
})
afterEach(() => brauzer.vernut())

test('снимок камеры 4032×3024 уменьшается до 1600×1200 и уходит JPEG', async () => {
  expect(razmerPosleUmensheniya(4032, 3024)).toEqual({ shirina: 1600, vysota: 1200 })

  const { foto, prevyu } = await umenshitFoto(snimok(4032, 3024))

  expect(foto.type).toBe('image/jpeg')
  expect(await soderzhimoe(foto)).toBe('jpeg 1600x1200')
  expect(brauzer.risovanie).toHaveBeenCalledWith(expect.any(HTMLImageElement), 0, 0, 1600, 1200)
  expect(brauzer.kachestva).toEqual([0.85])
  // Превью — адресом data: — его пропускает политика безопасности страницы.
  expect(prevyu).toMatch(/^data:image\/jpeg;base64,/)
})

test('портретный снимок — уменьшается по длинной стороне, вертикальной', async () => {
  expect(razmerPosleUmensheniya(3024, 4032)).toEqual({ shirina: 1200, vysota: 1600 })

  const { foto } = await umenshitFoto(snimok(3024, 4032))

  expect(await soderzhimoe(foto)).toBe('jpeg 1200x1600')
})

test('маленькое фото не трогается — не растягивается', async () => {
  expect(razmerPosleUmensheniya(800, 600)).toEqual({ shirina: 800, vysota: 600 })
  expect(razmerPosleUmensheniya(1600, 900)).toEqual({ shirina: 1600, vysota: 900 })

  const { foto } = await umenshitFoto(snimok(800, 600))

  // Размер прежний; JPEG — всё равно: скриншот бывает PNG, а сервер берёт
  // только JPEG.
  expect(await soderzhimoe(foto)).toBe('jpeg 800x600')
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

test('превью — из уменьшенного фото, а не из снимка камеры', async () => {
  // Снимок камеры адресом data: — до 8 МБ строки и 12 Мп в памяти на всё
  // время шага; превью уменьшенного — сотни килобайт, и это ровно то, что ушло.
  const { prevyu } = await umenshitFoto(snimok(4032, 3024))

  expect(prevyu).toMatch(/^data:image\/jpeg;base64,/)
  expect(atob(prevyu.slice(prevyu.indexOf(',') + 1))).toBe('jpeg 1600x1200')
})

test('под фото — белый фон, а холст после уменьшения освобождается', async () => {
  await umenshitFoto(snimok(4032, 3024))

  // PNG с прозрачностью без заливки стал бы в JPEG чёрным.
  expect(brauzer.poryadok).toEqual(['zalivka #ffffff 0,0,1600x1200', 'risovanie'])
  // Холст 1600×1200 — ещё 7 МБ памяти телефона, пока его не соберёт мусорщик.
  expect(brauzer.holsty).toHaveLength(1)
  expect(brauzer.holsty[0]).toMatchObject({ width: 0, height: 0 })
})

test.each([
  ['холст без кисти', 'netKisti'],
  ['JPEG не получился', 'netFoto'],
] as const)('не хватило памяти (%s) — свой текст, а не совет про HEIC', async (_, sboy) => {
  brauzer.pamyat[sboy] = true

  const otkaz = umenshitFoto(snimok(4032, 3024))

  await expect(otkaz).rejects.toBeInstanceOf(NeHvatiloPamyati)
  await expect(otkaz).rejects.toThrow(
    'Не хватило памяти телефона — закройте другие приложения и попробуйте ещё раз',
  )
  expect(brauzer.holsty[0]).toMatchObject({ width: 0, height: 0 })
})
