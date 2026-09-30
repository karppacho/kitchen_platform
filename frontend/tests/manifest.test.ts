import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from 'vitest'

// Файлы читаются с диска: манифест и index.html — не React, их проверяет
// только этот тест, а браузер молча проигнорировал бы неверный манифест.
const KOREN = join(__dirname, '..')
const manifest = () => JSON.parse(readFileSync(join(KOREN, 'public', 'manifest.webmanifest'), 'utf8')) as Record<string, unknown>
const html = () => readFileSync(join(KOREN, 'index.html'), 'utf8')

test('манифест: имя, запуск с блюд, окно без адресной строки, цвета из палитры', () => {
  const m = manifest()
  expect(m.name).toBe('Кухня')
  expect(m.short_name).toBe('Кухня')
  expect(m.start_url).toBe('/dishes')
  expect(m.scope).toBe('/')
  expect(m.display).toBe('standalone')
  expect(m.lang).toBe('ru')
  expect(m.background_color).toBe('#f2f2ef')
  expect(m.theme_color).toBe('#fbfbf9')
})

test('иконки из манифеста лежат в public, есть maskable и 512', () => {
  const icons = manifest().icons as { src: string; sizes: string; type: string; purpose?: string }[]
  expect(icons.length).toBeGreaterThanOrEqual(3)
  for (const icon of icons) {
    expect(existsSync(join(KOREN, 'public', icon.src))).toBe(true)
    expect(icon.type).toBe('image/png')
  }
  expect(icons.some((i) => i.sizes === '512x512' && i.purpose === 'maskable')).toBe(true)
  expect(icons.some((i) => i.sizes === '192x192')).toBe(true)
  expect(existsSync(join(KOREN, 'public', 'apple-touch-icon.png'))).toBe(true)
  expect(existsSync(join(KOREN, 'public', 'favicon.svg'))).toBe(true)
})

test('index.html подключает манифест, иконки, цвет строки состояния и вырез экрана', () => {
  const h = html()
  expect(h).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest"/)
  expect(h).toMatch(/<link rel="icon" href="\/favicon\.svg" type="image\/svg\+xml"/)
  expect(h).toMatch(/<link rel="apple-touch-icon" href="\/apple-touch-icon\.png"/)
  expect(h).toMatch(/<meta name="theme-color" content="#fbfbf9"/)
  expect(h).toMatch(/viewport-fit=cover/)
  expect(h).toMatch(/<meta name="mobile-web-app-capable" content="yes"/)
})
