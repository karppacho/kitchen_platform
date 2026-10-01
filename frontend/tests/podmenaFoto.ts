import { Blob as NodeBlob, File as NodeFile } from 'node:buffer'
import { vi } from 'vitest'

/**
 * Фото «с камеры» для тестов — и то, чего в jsdom нет: разбор картинки и холст.
 *
 * Содержимое «снимка» — его размер: «foto 4032x3024». Подменённый браузер
 * открывает только такие и рисует на холсте JPEG с текстом «jpeg ШxВ», — по
 * нему тест видит, что на сервер ушло уменьшенное фото, а не исходное.
 */

/** File и FormData из jsdom. FileReader jsdom читает только File jsdom;
 *  берутся при загрузке модуля — до подмен в тесте загрузки. */
const JsdomFile = globalThis.File
const JsdomFormData = globalThis.FormData

/** Node-двойник каждого снимка — то же содержимое в File Node: FormData Node,
 *  через которую тест шлёт фото, File jsdom не принимает. */
const dvoiniki = new WeakMap<Blob, Blob>()

export function snimok(shirina: number, vysota: number): File {
  const soderzhimoe = `foto ${shirina}x${vysota}`
  const fail = new JsdomFile([soderzhimoe], 'IMG_0001.jpg', { type: 'image/jpeg' })
  const dvoinik = new NodeFile([soderzhimoe], 'IMG_0001.jpg', { type: 'image/jpeg' })
  dvoiniki.set(fail, dvoinik as unknown as Blob)
  return fail
}

/**
 * В браузере fetch и FormData — одна семья. В тестах fetch из Node, а
 * FormData и File — из jsdom, и Node их не понимает: фото ушло бы пустым.
 * На время теста — FormData и File Node; снимок jsdom уходит своим
 * двойником, с тем же содержимым. Возвращает, как было.
 */
export async function podmenitFormData(): Promise<() => void> {
  const forma = await new Response('a=1', {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  }).formData()
  const NodeFormData = forma.constructor as typeof FormData
  class FormDataSDvoinikami extends NodeFormData {
    append(imya: string, znachenie: string | Blob, imyaFaila?: string): void {
      if (typeof znachenie === 'string') super.append(imya, znachenie)
      else super.append(imya, dvoiniki.get(znachenie) ?? znachenie, imyaFaila)
    }
  }
  globalThis.FormData = FormDataSDvoinikami
  globalThis.File = NodeFile as unknown as typeof File
  return () => {
    globalThis.FormData = JsdomFormData
    globalThis.File = JsdomFile
  }
}

/** Снимок, который браузер открыть не может, — HEIC там, где его не понимают. */
export function heic(): File {
  return new JsdomFile(['ftypheic'], 'IMG_0002.HEIC', { type: 'image/heic' })
}

function razmer(kartinka: HTMLImageElement): [number, number] {
  const adres = kartinka.src
  const soderzhimoe = atob(adres.slice(adres.indexOf(',') + 1))
  const najdeno = /^foto (\d+)x(\d+)$/.exec(soderzhimoe)
  return najdeno ? [Number(najdeno[1]), Number(najdeno[2])] : [0, 0]
}

export function podmenitBrauzer() {
  /** Что нарисовано на холсте: картинка, x, y, ширина, высота. */
  const risovanie = vi.fn()
  /** С каким качеством просили JPEG. */
  const kachestva: unknown[] = []

  const prezhnee = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'decode')
  HTMLImageElement.prototype.decode = function (this: HTMLImageElement) {
    return razmer(this)[0] > 0
      ? Promise.resolve()
      : Promise.reject(new DOMException('The source image cannot be decoded.', 'EncodingError'))
  }
  vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockImplementation(function (
    this: HTMLImageElement,
  ) {
    return razmer(this)[0]
  })
  vi.spyOn(HTMLImageElement.prototype, 'naturalHeight', 'get').mockImplementation(function (
    this: HTMLImageElement,
  ) {
    return razmer(this)[1]
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({
    drawImage: risovanie,
  })) as unknown as HTMLCanvasElement['getContext'])
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (
    this: HTMLCanvasElement,
    gotovo: BlobCallback,
    tip?: string,
    kachestvo?: unknown,
  ) {
    kachestva.push(kachestvo)
    // Blob из Node: его примет FormData Node, через которую тест шлёт фото.
    gotovo(new NodeBlob([`jpeg ${this.width}x${this.height}`], { type: tip }) as unknown as Blob)
  })

  return {
    risovanie,
    kachestva,
    vernut() {
      vi.restoreAllMocks()
      if (prezhnee) Object.defineProperty(HTMLImageElement.prototype, 'decode', prezhnee)
      else delete (HTMLImageElement.prototype as { decode?: unknown }).decode
    },
  }
}
