import type { Draft } from '../../api/types'
import { RamkaShaga, useShag } from './RamkaShaga'
import { type PodpisiSnimka, useZagruzkaSnimka, ZagruzkaSnimka } from './ZagruzkaSnimka'

type VidProdukta = 'package' | 'before' | 'after'

/** Слоты — как колонки листа: «В упаковке», «До обработки», «После обработки». */
const SLOTY: readonly { vid: VidProdukta; nazvanie: string; podpisi: PodpisiSnimka }[] = [
  { vid: 'package', nazvanie: 'В упаковке', podpisi: podpisiSlota('Фото в упаковке') },
  { vid: 'before', nazvanie: 'До обработки', podpisi: podpisiSlota('Фото до обработки') },
  { vid: 'after', nazvanie: 'После обработки', podpisi: podpisiSlota('Фото после обработки') },
]

function podpisiSlota(foto: string): PodpisiSnimka {
  return { kamera: 'Сфотографировать', galereya: 'Из галереи', drugoe: 'Другое из галереи', foto }
}

/**
 * Шаг 7. Фото продукта — три слота, все необязательны.
 *
 * Каждое фото уменьшается и загружается само по себе, так же как этикетка.
 * Фото нет — главная кнопка «Пропустить», есть — «Далее»; ведут обе к
 * описанию. Пока хоть одно фото готовится или загружается, «Назад» и
 * главная кнопка ждут: ушедший шаг не узнал бы, дошло ли фото.
 */
export function ShagFoto({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik)
  const zagruzki = {
    package: useZagruzkaSnimka(chernovik, 'package'),
    before: useZagruzkaSnimka(chernovik, 'before'),
    after: useZagruzkaSnimka(chernovik, 'after'),
  }
  const idyot = SLOTY.some(({ vid }) => zagruzki[vid].idyot)
  const estFoto = SLOTY.some(({ vid }) => chernovik.photos[vid])

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee
      onDalee={() => upravlenie.dalee({})}
      tekstDalee={estFoto ? 'Далее' : 'Пропустить'}
      zhdyom={idyot}
    >
      <p className="kartochka-poyasnenie">
        Необязательно: как продукт выглядит в упаковке, до и после обработки. Любое фото можно
        пропустить.
      </p>
      {SLOTY.map(({ vid, nazvanie, podpisi }) => (
        <fieldset key={vid} className="kartochka-gruppa">
          <legend>{nazvanie}</legend>
          <ZagruzkaSnimka
            chernovik={chernovik}
            vid={vid}
            zagruzka={zagruzki[vid]}
            podpisi={podpisi}
            glavnaya={false}
            malenkoe
          />
        </fieldset>
      ))}
    </RamkaShaga>
  )
}
