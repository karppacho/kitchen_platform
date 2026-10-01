import type { Raspoznavanie } from '../../api/kartochki'
import type { Draft } from '../../api/types'
import { RamkaShaga, useShag } from './RamkaShaga'
import { type PodpisiSnimka, useZagruzkaSnimka, ZagruzkaSnimka } from './ZagruzkaSnimka'

/** Подписи фото этикетки — и на шаге 4, и в «Переснять» на проверке. */
export const PODPISI_ETIKETKI: PodpisiSnimka = {
  kamera: 'Сфотографировать этикетку',
  galereya: 'Выбрать из галереи',
  drugoe: 'Другое фото из галереи',
  foto: 'Фото этикетки',
}

/**
 * Шаг 4. Фото этикетки — камера или галерея.
 *
 * Фото уменьшается в браузере и загружается; загрузилось — распознавание
 * запускается само, а повар переходит на проверку: там видно, как оно идёт,
 * и туда же лягут прочитанные поля. Пока фото готовится и загружается,
 * «Назад» и «Далее» ждут: ушедший шаг не запустил бы распознавание.
 */
export function ShagEtiketka({
  chernovik,
  raspoznavanie,
}: {
  chernovik: Draft
  raspoznavanie: Raspoznavanie
}) {
  const upravlenie = useShag(chernovik)
  const zagruzka = useZagruzkaSnimka(chernovik, 'label', {
    posle: () => {
      raspoznavanie.mutate(chernovik.id)
      upravlenie.dalee({})
    },
  })

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={chernovik.photos.label}
      onDalee={() => upravlenie.dalee({})}
      zhdyom={zagruzka.idyot}
    >
      <p className="kartochka-poyasnenie">
        Сфотографируйте этикетку так, чтобы читались состав, КБЖУ и сроки, — их распознаем с
        фото, вам останется проверить.
      </p>
      <ZagruzkaSnimka
        chernovik={chernovik}
        vid="label"
        zagruzka={zagruzka}
        podpisi={PODPISI_ETIKETKI}
      />
      {!chernovik.photos.label && (
        <p className="kartochka-poyasnenie">«Далее» станет доступна, когда фото загрузится.</p>
      )}
    </RamkaShaga>
  )
}
