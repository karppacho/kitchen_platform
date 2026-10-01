/**
 * Фото с телефона — уменьшить в браузере, до отправки.
 *
 * Снимок камеры — 12 Мп и 3–6 МБ: по мобильной сети у плиты это долго, а
 * этикетке хватает 1600 px по длинной стороне — мелкий шрифт состава ещё
 * читается. Уменьшенное уходит JPEG: сервер принимает только его (не PNG
 * скриншота и не HEIC), а заодно на сервер не уходят метаданные снимка — в
 * том числе место съёмки.
 *
 * Фото открывается через <img> по адресу data: — так браузер сам
 * поворачивает снимок по метке камеры, а политика безопасности страницы
 * пропускает data:, но не blob:.
 */

/** Длинная сторона уменьшенного фото, px. */
export const MAKS_STORONA = 1600

/** Качество JPEG: этикетка читается, фото — сотни килобайт. */
export const KACHESTVO_JPEG = 0.85

export type Razmer = { shirina: number; vysota: number }

/** Размер после уменьшения: длинная сторона — не больше `maks`, пропорции
 *  те же. Маленькое не растягивается. */
export function razmerPosleUmensheniya(
  shirina: number,
  vysota: number,
  maks: number = MAKS_STORONA,
): Razmer {
  const dlinnaya = Math.max(shirina, vysota)
  if (dlinnaya <= maks) return { shirina, vysota }
  const dolya = maks / dlinnaya
  // Полоска в пиксель высотой не должна стать нулём: холст нулевого размера
  // не даёт картинки вовсе.
  return {
    shirina: Math.max(1, Math.round(shirina * dolya)),
    vysota: Math.max(1, Math.round(vysota * dolya)),
  }
}

/** Фото не подготовилось. Текст — для повара, как есть. */
export class OshibkaFoto extends Error {}

/** Браузер не смог открыть фото. Чаще всего это HEIC — так снимает iPhone,
 *  а открыть его умеет не каждый браузер. */
export class FotoNeOtkrylos extends OshibkaFoto {
  constructor() {
    super(
      'Не получилось открыть фото. Если вы снимаете на iPhone, фото может быть в формате ' +
        'HEIC: в «Настройках» → «Камера» → «Форматы» выберите «Наиболее совместимый» и ' +
        'сфотографируйте ещё раз. Или выберите другое фото.',
    )
    this.name = 'FotoNeOtkrylos'
  }
}

/** Фото открылось, а холст или JPEG не получились: браузеру не хватило
 *  памяти. Формат тут ни при чём — совет про HEIC только запутал бы. */
export class NeHvatiloPamyati extends OshibkaFoto {
  constructor() {
    super('Не хватило памяти телефона — закройте другие приложения и попробуйте ещё раз')
    this.name = 'NeHvatiloPamyati'
  }
}

export type UmenshennoeFoto = {
  /** Что уйдёт на сервер — JPEG не больше 1600 px. */
  foto: Blob
  /** Адрес data: уменьшенного фото — для превью на экране. */
  prevyu: string
}

/** Уменьшить фото до 1600 px по длинной стороне и перевести в JPEG.
 *  Не открылось — :class:`FotoNeOtkrylos`, не хватило памяти —
 *  :class:`NeHvatiloPamyati`. */
export async function umenshitFoto(fail: Blob): Promise<UmenshennoeFoto> {
  const kartinka = new Image()
  kartinka.src = await prochitatKakAdres(fail)
  try {
    await kartinka.decode()
  } catch {
    throw new FotoNeOtkrylos()
  }
  const { naturalWidth, naturalHeight } = kartinka
  if (!naturalWidth || !naturalHeight) throw new FotoNeOtkrylos()

  const foto = await narisovatJpeg(
    kartinka,
    razmerPosleUmensheniya(naturalWidth, naturalHeight),
  )
  // Превью — из уменьшенного: снимок камеры адресом data: — это до 8 МБ
  // строки и 12 Мп в памяти на всё время шага, а уменьшенное — сотни
  // килобайт, и на экране ровно то, что ушло на сервер.
  return { foto, prevyu: await prochitatKakAdres(foto) }
}

/** Нарисовать снимок на холсте нужного размера и взять JPEG. */
async function narisovatJpeg(
  kartinka: HTMLImageElement,
  { shirina, vysota }: Razmer,
): Promise<Blob> {
  const holst = document.createElement('canvas')
  holst.width = shirina
  holst.height = vysota
  try {
    const kist = holst.getContext('2d')
    if (!kist) throw new NeHvatiloPamyati()
    // Прозрачное (PNG скриншота) в JPEG без фона стало бы чёрным.
    kist.fillStyle = '#ffffff'
    kist.fillRect(0, 0, shirina, vysota)
    kist.drawImage(kartinka, 0, 0, shirina, vysota)
    const foto = await new Promise<Blob | null>((gotovo) =>
      holst.toBlob(gotovo, 'image/jpeg', KACHESTVO_JPEG),
    )
    if (!foto) throw new NeHvatiloPamyati()
    return foto
  } finally {
    // Холст 1600×1200 — ещё 7 МБ памяти телефона, пока его не соберут:
    // на старом телефоне следующее фото могло бы уже не поместиться.
    holst.width = 0
    holst.height = 0
  }
}

function prochitatKakAdres(fail: Blob): Promise<string> {
  return new Promise((gotovo, otkaz) => {
    const chtec = new FileReader()
    chtec.onload = () => {
      if (typeof chtec.result === 'string') gotovo(chtec.result)
      else otkaz(new FotoNeOtkrylos())
    }
    chtec.onerror = () => otkaz(new FotoNeOtkrylos())
    chtec.readAsDataURL(fail)
  })
}
