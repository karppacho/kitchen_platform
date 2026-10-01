import { useState } from 'react'

import { ApiError } from '../../api/client'
import { POVTOROV_ZAGRUZKI, useZagruzkaFoto, type Raspoznavanie } from '../../api/kartochki'
import type { Draft } from '../../api/types'
import { FotoNeOtkrylos, umenshitFoto, type UmenshennoeFoto } from '../../domain/foto'
import { FotoVybor } from '../../ui/FotoVybor'
import { RamkaShaga, useShag } from './RamkaShaga'

/** Все попытки загрузки оборвались. Фото не теряется — его можно загрузить
 *  ещё раз тем же: снимок с камеры в галерее не лежит. */
const OBORVALOS =
  'Фото не загрузилось — связь обрывается. Проверьте связь и нажмите «Загрузить ещё раз».'

export type ZagruzkaEtiketki = {
  vybrat: (fail: File) => void
  /** Загрузить то же фото ещё раз — после отказа загрузки. */
  povtorit: (() => void) | undefined
  /** Фото готовится или загружается. */
  idyot: boolean
  /** Что сейчас происходит — словами для повара. */
  sostoyanie: string | null
  oshibka: string | null
  /** Только что выбранное фото — адресом data:. */
  prevyu: string | null
}

function tekstOtkazaZagruzki(oshibka: unknown): string {
  if (oshibka instanceof ApiError && oshibka.status === 0) return OBORVALOS
  return oshibka instanceof Error ? oshibka.message : OBORVALOS
}

/**
 * Фото этикетки: уменьшить в браузере, загрузить (с автоповторами), затем —
 * `posle` (запустить распознавание). Общее у шага «Фото этикетки» и у
 * «Переснять» на проверке. `pered` — перед новым фото: прежний итог
 * распознавания на экране уже не о нём.
 */
export function useZagruzkaEtiketki(
  chernovik: Draft,
  { pered, posle }: { pered?: () => void; posle: () => void },
): ZagruzkaEtiketki {
  const [gotovim, zadatGotovim] = useState(false)
  const [popytka, zadatPopytku] = useState(1)
  const [gotovoe, zadatGotovoe] = useState<UmenshennoeFoto | null>(null)
  const [neOtkrylos, zadatNeOtkrylos] = useState<string | null>(null)
  const zagruzka = useZagruzkaFoto(zadatPopytku)

  function zagruzit(foto: Blob) {
    zadatPopytku(1)
    zagruzka.mutate({ id: chernovik.id, vid: 'label', foto }, { onSuccess: posle })
  }

  async function vybrat(fail: File) {
    pered?.()
    zagruzka.reset()
    zadatNeOtkrylos(null)
    zadatGotovim(true)
    try {
      const umenshennoe = await umenshitFoto(fail)
      zadatGotovoe(umenshennoe)
      // На сервер — уменьшенное: исходник с камеры в 5–10 раз тяжелее.
      zagruzit(umenshennoe.foto)
    } catch (oshibka) {
      zadatGotovoe(null)
      zadatNeOtkrylos((oshibka instanceof FotoNeOtkrylos ? oshibka : new FotoNeOtkrylos()).message)
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

  return {
    vybrat: (fail) => void vybrat(fail),
    povtorit: gotovoe && zagruzka.isError ? () => zagruzit(gotovoe.foto) : undefined,
    idyot: gotovim || zagruzka.isPending,
    sostoyanie,
    oshibka: neOtkrylos ?? (zagruzka.isError ? tekstOtkazaZagruzki(zagruzka.error) : null),
    prevyu: gotovoe?.prevyu ?? null,
  }
}

/** Адрес фото этикетки черновика — через прокси сервера: папка в хранилище
 *  закрыта. Время правки в адресе — после замены фото браузер не покажет
 *  прежнее с того же адреса. */
function adresEtiketki(chernovik: Draft): string {
  const id = encodeURIComponent(chernovik.id)
  return `/api/cards/drafts/${id}/photos/label?v=${encodeURIComponent(chernovik.updated_at)}`
}

/** Фото этикетки, выбор нового и ход загрузки. */
export function ZagruzkaEtiketki({
  chernovik,
  zagruzka,
  zablokirovano = false,
}: {
  chernovik: Draft
  zagruzka: ZagruzkaEtiketki
  zablokirovano?: boolean
}) {
  // Адрес — на время шага: прокси каждый раз качает фото из хранилища, а
  // черновик меняется и без новой этикетки (лёг итог распознавания). Новое
  // фото, снятое здесь же, показывается своим превью.
  const [adresSServera] = useState(() => adresEtiketki(chernovik))
  const estFoto = chernovik.photos.label || zagruzka.prevyu !== null
  const adres = zagruzka.prevyu ?? (chernovik.photos.label ? adresSServera : null)

  return (
    <div className="kartochka-etiketka">
      {adres !== null && <img className="kartochka-foto" src={adres} alt="Фото этикетки" />}
      <FotoVybor
        kamera={estFoto ? 'Переснять' : 'Сфотографировать этикетку'}
        galereya={estFoto ? 'Другое фото из галереи' : 'Выбрать из галереи'}
        onVybrano={zagruzka.vybrat}
        zablokirovano={zablokirovano || zagruzka.idyot}
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

/**
 * Шаг 4. Фото этикетки — камера или галерея.
 *
 * Фото уменьшается в браузере и загружается; загрузилось — распознавание
 * запускается само, а повар переходит на проверку: там видно, как оно идёт,
 * и туда же лягут прочитанные поля.
 */
export function ShagEtiketka({
  chernovik,
  raspoznavanie,
}: {
  chernovik: Draft
  raspoznavanie: Raspoznavanie
}) {
  const upravlenie = useShag(chernovik)
  const zagruzka = useZagruzkaEtiketki(chernovik, {
    posle: () => {
      raspoznavanie.mutate(chernovik.id)
      upravlenie.dalee({})
    },
  })

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={chernovik.photos.label && !zagruzka.idyot}
      onDalee={() => upravlenie.dalee({})}
    >
      <p className="kartochka-poyasnenie">
        Сфотографируйте этикетку так, чтобы читались состав, КБЖУ и сроки, — их распознаем с
        фото, вам останется проверить.
      </p>
      <ZagruzkaEtiketki chernovik={chernovik} zagruzka={zagruzka} />
      {!chernovik.photos.label && (
        <p className="kartochka-poyasnenie">«Далее» станет доступна, когда фото загрузится.</p>
      )}
    </RamkaShaga>
  )
}
