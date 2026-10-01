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

/** Браузер не смог открыть фото. Чаще всего это HEIC — так снимает iPhone,
 *  а открыть его умеет не каждый браузер. */
export class FotoNeOtkrylos extends Error {
  constructor() {
    super(
      'Не получилось открыть фото. Если вы снимаете на iPhone, фото может быть в формате ' +
        'HEIC: в «Настройках» → «Камера» → «Форматы» выберите «Наиболее совместимый» и ' +
        'сфотографируйте ещё раз. Или выберите другое фото.',
    )
    this.name = 'FotoNeOtkrylos'
  }
}

export type UmenshennoeFoto = {
  /** Что уйдёт на сервер — JPEG не больше 1600 px. */
  foto: Blob
  /** Адрес data: для превью на экране. */
  prevyu: string
}

/** Уменьшить фото до 1600 px по длинной стороне и перевести в JPEG.
 *  Не открылось — :class:`FotoNeOtkrylos`. */
export async function umenshitFoto(fail: Blob): Promise<UmenshennoeFoto> {
  const adres = await prochitatKakAdres(fail)
  const kartinka = new Image()
  kartinka.src = adres
  try {
    await kartinka.decode()
  } catch {
    throw new FotoNeOtkrylos()
  }
  const { naturalWidth, naturalHeight } = kartinka
  if (!naturalWidth || !naturalHeight) throw new FotoNeOtkrylos()

  const { shirina, vysota } = razmerPosleUmensheniya(naturalWidth, naturalHeight)
  const holst = document.createElement('canvas')
  holst.width = shirina
  holst.height = vysota
  const kist = holst.getContext('2d')
  if (!kist) throw new FotoNeOtkrylos()
  kist.drawImage(kartinka, 0, 0, shirina, vysota)
  const foto = await new Promise<Blob | null>((gotovo) =>
    holst.toBlob(gotovo, 'image/jpeg', KACHESTVO_JPEG),
  )
  if (!foto) throw new FotoNeOtkrylos()
  return { foto, prevyu: adres }
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
