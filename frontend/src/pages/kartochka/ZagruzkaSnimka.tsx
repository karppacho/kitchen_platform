import { useState } from 'react'

import { ApiError } from '../../api/client'
import { POVTOROV_ZAGRUZKI, useZagruzkaFoto } from '../../api/kartochki'
import type { Draft, VidFoto } from '../../api/types'
import {
  FotoNeOtkrylos,
  OshibkaFoto,
  umenshitFoto,
  type UmenshennoeFoto,
} from '../../domain/foto'
import { FotoVybor } from '../../ui/FotoVybor'

/** Все попытки загрузки оборвались. Фото не теряется — его можно загрузить
 *  ещё раз тем же: снимок с камеры в галерее не лежит. */
const OBORVALOS =
  'Фото не загрузилось — связь обрывается. Проверьте связь и нажмите «Загрузить ещё раз».'

/** Отказы, после которых то же фото загружать бессмысленно: оно больше
 *  8 МБ или не JPEG — нужно другое фото, а не повтор. */
const TO_ZHE_NE_POMOZHET = [413, 415]

export type ZagruzkaSnimka = {
  vybrat: (fail: File) => void
  /** Загрузить то же фото ещё раз — после отказа, который повтор лечит. */
  povtorit: (() => void) | undefined
  /** Фото готовится или загружается. */
  idyot: boolean
  /** Что сейчас происходит — словами для повара. */
  sostoyanie: string | null
  oshibka: string | null
  /** Только что выбранное фото, уменьшенное, — адресом data:. */
  prevyu: string | null
}

function tekstOtkazaZagruzki(oshibka: unknown): string {
  if (oshibka instanceof ApiError && oshibka.status === 0) return OBORVALOS
  return oshibka instanceof Error ? oshibka.message : OBORVALOS
}

/**
 * Фото в слот черновика: уменьшить в браузере, загрузить (с автоповторами),
 * затем — `posle`. Общее у этикетки (и «Переснять» на проверке) и у трёх
 * фото продукта. `pered` — перед новым фото.
 */
export function useZagruzkaSnimka(
  chernovik: Draft,
  vid: VidFoto,
  { pered, posle }: { pered?: () => void; posle?: () => void } = {},
): ZagruzkaSnimka {
  const [gotovim, zadatGotovim] = useState(false)
  const [popytka, zadatPopytku] = useState(1)
  const [gotovoe, zadatGotovoe] = useState<UmenshennoeFoto | null>(null)
  const [neGotovo, zadatNeGotovo] = useState<string | null>(null)
  const zagruzka = useZagruzkaFoto(zadatPopytku)

  function zagruzit(foto: Blob) {
    zadatPopytku(1)
    zagruzka.mutate({ id: chernovik.id, vid, foto }, posle && { onSuccess: posle })
  }

  async function vybrat(fail: File) {
    pered?.()
    zagruzka.reset()
    zadatNeGotovo(null)
    zadatGotovim(true)
    try {
      const umenshennoe = await umenshitFoto(fail)
      zadatGotovoe(umenshennoe)
      // На сервер — уменьшенное: исходник с камеры в 5–10 раз тяжелее.
      zagruzit(umenshennoe.foto)
    } catch (oshibka) {
      zadatGotovoe(null)
      zadatNeGotovo((oshibka instanceof OshibkaFoto ? oshibka : new FotoNeOtkrylos()).message)
    } finally {
      zadatGotovim(false)
    }
  }

  let sostoyanie: string | null = null
  if (gotovim) sostoyanie = 'Готовим фото…'
  else if (zagruzka.isPending) {
    sostoyanie =
      popytka === 1
        ? 'Загружаем фото…'
        : `Связь прервалась — пробуем ещё раз (попытка ${popytka} из ${POVTOROV_ZAGRUZKI + 1})…`
  }

  const otkaz = zagruzka.error
  const povtorPomozhet = !(otkaz instanceof ApiError && TO_ZHE_NE_POMOZHET.includes(otkaz.status))

  return {
    vybrat: (fail) => void vybrat(fail),
    povtorit:
      gotovoe && zagruzka.isError && povtorPomozhet ? () => zagruzit(gotovoe.foto) : undefined,
    idyot: gotovim || zagruzka.isPending,
    sostoyanie,
    oshibka: neGotovo ?? (zagruzka.isError ? tekstOtkazaZagruzki(otkaz) : null),
    prevyu: gotovoe?.prevyu ?? null,
  }
}

/** Адрес фото черновика — через прокси сервера: папка в хранилище закрыта.
 *  Время правки в адресе — после замены фото браузер не покажет прежнее с
 *  того же адреса. */
function adresFoto(chernovik: Draft, vid: VidFoto): string {
  const id = encodeURIComponent(chernovik.id)
  return `/api/cards/drafts/${id}/photos/${vid}?v=${encodeURIComponent(chernovik.updated_at)}`
}

export type PodpisiSnimka = {
  /** Кнопка камеры, пока фото нет: «Сфотографировать этикетку». */
  kamera: string
  /** Кнопка галереи, пока фото нет. */
  galereya: string
  /** Кнопка галереи, когда фото уже есть. */
  drugoe: string
  /** Подпись картинки для читалки экрана. */
  foto: string
}

/** Фото слота, выбор нового и ход загрузки. */
export function ZagruzkaSnimka({
  chernovik,
  vid,
  zagruzka,
  podpisi,
  zablokirovano = false,
  glavnaya,
  malenkoe = false,
}: {
  chernovik: Draft
  vid: VidFoto
  zagruzka: ZagruzkaSnimka
  podpisi: PodpisiSnimka
  zablokirovano?: boolean
  /** Камера — главная кнопка экрана. По умолчанию — пока фото нет. */
  glavnaya?: boolean
  /** Фото поменьше: под ним поля, которые не должны уехать за край. */
  malenkoe?: boolean
}) {
  // Адрес — на время шага: прокси каждый раз качает фото из хранилища, а
  // черновик меняется и без нового фото (лёг итог распознавания). Новое
  // фото, снятое здесь же, показывается своим превью.
  const [adresSServera] = useState(() => adresFoto(chernovik, vid))
  const estFoto = chernovik.photos[vid] || zagruzka.prevyu !== null
  const adres = zagruzka.prevyu ?? (chernovik.photos[vid] ? adresSServera : null)

  return (
    <div className="kartochka-etiketka">
      {adres !== null && (
        <img
          className={malenkoe ? 'kartochka-foto kartochka-foto-malenkoe' : 'kartochka-foto'}
          src={adres}
          alt={podpisi.foto}
        />
      )}
      <FotoVybor
        kamera={estFoto ? 'Переснять' : podpisi.kamera}
        galereya={estFoto ? podpisi.drugoe : podpisi.galereya}
        onVybrano={zagruzka.vybrat}
        zablokirovano={zablokirovano || zagruzka.idyot}
        glavnaya={glavnaya ?? !estFoto}
      />
      <div role="status" className="kartochka-zhivaya">
        {zagruzka.sostoyanie !== null && (
          <p className="kartochka-poyasnenie">{zagruzka.sostoyanie}</p>
        )}
      </div>
      {zagruzka.oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {zagruzka.oshibka}
        </p>
      )}
      {zagruzka.povtorit && (
        <div className="kartochka-knopki">
          <button type="button" onClick={zagruzka.povtorit}>
            Загрузить ещё раз
          </button>
        </div>
      )}
    </div>
  )
}
