import { useEffect, useRef, useState } from 'react'

import { ApiError, NE_POLUCHILOS } from '../../api/client'
import { useUdalenieFoto } from '../../api/kartochki'
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

const NE_UBRALOS = 'Фото не убралось — проверьте связь и нажмите «Убрать» ещё раз'

function tekstOtkazaUdaleniya(oshibka: unknown): string {
  if (!(oshibka instanceof ApiError) || oshibka.status === 0) return NE_UBRALOS
  return oshibka.message === NE_POLUCHILOS ? NE_UBRALOS : oshibka.message
}

/**
 * Шаг 7. Фото продукта — три слота, все необязательны.
 *
 * Каждое фото уменьшается и загружается само по себе, так же как этикетка.
 * Фото нет — главная кнопка «Пропустить», есть — «Далее»; ведут обе к
 * описанию. Случайное фото можно убрать. Пока хоть одно фото готовится,
 * загружается или убирается, «Назад» и главная кнопка ждут: ушедший шаг не
 * узнал бы, чем это кончилось.
 */
export function ShagFoto({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik)
  const zagruzki = {
    package: useZagruzkaSnimka(chernovik, 'package'),
    before: useZagruzkaSnimka(chernovik, 'before'),
    after: useZagruzkaSnimka(chernovik, 'after'),
  }
  const udalenie = useUdalenieFoto()
  const idyot = udalenie.isPending || SLOTY.some(({ vid }) => zagruzki[vid].idyot)
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
      {SLOTY.map(({ vid, nazvanie, podpisi }) => {
        const ubiraem = udalenie.variables?.vid === vid
        return (
          <fieldset key={vid} className="kartochka-gruppa">
            <legend>{nazvanie}</legend>
            <ZagruzkaSnimka
              chernovik={chernovik}
              vid={vid}
              zagruzka={zagruzki[vid]}
              podpisi={podpisi}
              zablokirovano={ubiraem && udalenie.isPending}
              glavnaya={false}
              malenkoe
            />
            {chernovik.photos[vid] && (
              <UbratFoto
                nazvanie={nazvanie}
                zablokirovano={zagruzki[vid].idyot || udalenie.isPending}
                oshibka={ubiraem && udalenie.isError ? tekstOtkazaUdaleniya(udalenie.error) : null}
                onUbrat={() =>
                  udalenie.mutate(
                    { id: chernovik.id, vid },
                    // Превью этой загрузки — уже не о том, что в черновике.
                    { onSuccess: () => zagruzki[vid].sbrosit() },
                  )
                }
              />
            )}
          </fieldset>
        )
      })}
    </RamkaShaga>
  )
}

/**
 * «Убрать» — только после вопроса. Переснять можно не всё: «до обработки»
 * после обработки уже не сфотографировать, а одно касание мимо стёрло бы
 * его. Вопрос открылся — фокус на безопасном ответе; закрылся — обратно.
 */
function UbratFoto({
  nazvanie,
  zablokirovano,
  oshibka,
  onUbrat,
}: {
  nazvanie: string
  zablokirovano: boolean
  oshibka: string | null
  onUbrat: () => void
}) {
  const [sprashivaem, sprosit] = useState(false)
  const net = useRef<HTMLButtonElement>(null)
  const ubrat = useRef<HTMLButtonElement>(null)
  const sprashivali = useRef(false)

  useEffect(() => {
    if (sprashivaem) {
      sprashivali.current = true
      net.current?.focus()
    } else if (sprashivali.current) {
      ubrat.current?.focus()
    }
  }, [sprashivaem])

  return (
    <>
      {sprashivaem ? (
        <div className="kartochka-podtverzhdenie">
          <p>{`Убрать фото «${nazvanie.toLocaleLowerCase('ru')}»? Вернуть его будет нельзя — только снять заново.`}</p>
          <div className="kartochka-knopki">
            <button type="button" ref={net} onClick={() => sprosit(false)}>
              Нет, оставить
            </button>
            <button
              type="button"
              className="kartochka-opasnaya"
              onClick={() => {
                sprosit(false)
                onUbrat()
              }}
            >
              Да, убрать
            </button>
          </div>
        </div>
      ) : (
        <div className="kartochka-knopki">
          <button type="button" ref={ubrat} disabled={zablokirovano} onClick={() => sprosit(true)}>
            Убрать
          </button>
        </div>
      )}
      {oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {oshibka}
        </p>
      )}
    </>
  )
}
