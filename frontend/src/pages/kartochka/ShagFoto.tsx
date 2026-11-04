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
  const udalenie = useUdalenieFoto()
  // Новое фото слота легло — прежний отказ удаления уже не о нём.
  const posleZagruzki = { posle: udalenie.reset }
  const zagruzki = {
    package: useZagruzkaSnimka(chernovik, 'package', posleZagruzki),
    before: useZagruzkaSnimka(chernovik, 'before', posleZagruzki),
    after: useZagruzkaSnimka(chernovik, 'after', posleZagruzki),
  }
  // Слот, куда вернуть фокус, когда фото убрано: кнопка «Убрать» исчезла
  // вместе с фото, и фокус иначе упал бы на страницу.
  const [fokusVSlot, zadatFokusVSlot] = useState<VidProdukta | null>(null)
  const slotyRef = useRef<Partial<Record<VidProdukta, HTMLFieldSetElement | null>>>({})
  useEffect(() => {
    if (fokusVSlot === null || udalenie.isPending) return
    slotyRef.current[fokusVSlot]?.querySelector<HTMLInputElement>('input[type="file"]')?.focus()
    zadatFokusVSlot(null)
  }, [fokusVSlot, udalenie.isPending])
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
          <fieldset
            key={vid}
            className="kartochka-gruppa"
            ref={(uzel) => {
              slotyRef.current[vid] = uzel
            }}
          >
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
                zagruzhaetsya={zagruzki[vid].idyot}
                oshibka={ubiraem && udalenie.isError ? tekstOtkazaUdaleniya(udalenie.error) : null}
                onUbrat={() =>
                  udalenie.mutate(
                    { id: chernovik.id, vid },
                    // Превью этой загрузки — уже не о том, что в черновике.
                    {
                      onSuccess: () => {
                        zagruzki[vid].sbrosit()
                        zadatFokusVSlot(vid)
                      },
                    },
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
  zagruzhaetsya,
  oshibka,
  onUbrat,
}: {
  nazvanie: string
  zablokirovano: boolean
  /** Слот грузится: убирать нечего, пока не легло новое фото. */
  zagruzhaetsya: boolean
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
              disabled={zablokirovano}
              onClick={() => {
                sprosit(false)
                onUbrat()
              }}
            >
              Да, убрать
            </button>
          </div>
          {zagruzhaetsya && <p className="kartochka-poyasnenie">Идёт загрузка…</p>}
        </div>
      ) : (
        <div className="kartochka-knopki">
          <button type="button" ref={ubrat} disabled={zablokirovano} onClick={() => sprosit(true)}>
            Убрать
          </button>
          {zagruzhaetsya && <span className="kartochka-poyasnenie">Идёт загрузка…</span>}
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
