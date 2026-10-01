import { useEffect, useRef, useState } from 'react'

import {
  NAZVANIYA_SHAGOV,
  SHAGI,
  useChernovik,
  useNachatChernovik,
  useOtpravka,
  useRaspoznavanie,
  type Otpravka,
  type Raspoznavanie,
} from '../api/kartochki'
import type { Draft } from '../api/types'
import { Sostoyanie } from '../ui/Sostoyanie'
import { PamyatShagovMastera } from './kartochka/RamkaShaga'
import { ShagEtiketka } from './kartochka/ShagEtiketka'
import { ShagFoto } from './kartochka/ShagFoto'
import { ShagItog, Zapisano } from './kartochka/ShagItog'
import { ShagKategoriya } from './kartochka/ShagKategoriya'
import { ShagNazvanie } from './kartochka/ShagNazvanie'
import { ShagOpisanie } from './kartochka/ShagOpisanie'
import { ShagPostavshchik } from './kartochka/ShagPostavshchik'
import { ShagProverka } from './kartochka/ShagProverka'
import { ShagSoglasovan } from './kartochka/ShagSoglasovan'
import './kartochka/kartochka.css'
import './pages.css'

/**
 * «Новый ингредиент» — мастер карточки для повара, с телефона.
 *
 * Черновик живёт на сервере, один на повара: телефон уснул, вкладку
 * закрыли — работа не пропала. Шаг на экране — всегда шаг черновика:
 * каждое «Далее» и «Назад» сохраняет его на сервере.
 */
export function NovyiIngredient() {
  const chernovik = useChernovik()
  // Отправка живёт здесь, над шагами: черновика после неё нет, а экран
  // «Записано в таблицу» и защита от второй отправки должны остаться.
  const otpravka = useOtpravka()
  // id черновика, с которым повар работает: начал его здесь или выбрал
  // «Продолжить». Черновик сменился (завели в другой вкладке) — снова вопрос.
  const [vRabote, zadatVRabote] = useState<string | null>(null)
  const nachat = useNachatChernovik((novyi) => {
    zadatVRabote(novyi.id)
    otpravka.sbrosit()
  })
  const oshibka = nachat.error instanceof Error ? nachat.error.message : null
  // Распознавание запускает шаг «Фото этикетки», а ждёт его итога шаг
  // «Проверка» — запрос живёт здесь, над шагами, и переживает смену шага.
  const raspoznavanie = useRaspoznavanie()
  // Новая карточка начата с экрана «Записано»: её первый шаг получает фокус.
  const [posleZapisi, zadatPosleZapisi] = useState(false)
  const nachatSnachala = (prezhniy: string | null) => {
    zadatPosleZapisi(false)
    nachat.mutate(prezhniy)
  }

  // Пока идёт отправка, экран держит отправляемый черновик: перечитывание
  // уже может ответить «черновика нет» — строка легла, а ответ ещё в пути.
  const dannye = otpravka.chernovik ?? chernovik.data
  let ekran
  if (otpravka.otvet !== null) {
    // «Записано» держится, пока заводится новая карточка; не завелась —
    // экран вернётся к черновику, и отказ будет виден там.
    ekran = (
      <Zapisano
        otvet={otpravka.otvet}
        onEshche={() => {
          zadatPosleZapisi(true)
          nachat.mutate(null, { onError: otpravka.sbrosit })
        }}
        zanyato={nachat.isPending}
      />
    )
  } else if (dannye === undefined) {
    ekran = <Sostoyanie query={chernovik} />
  } else if (dannye === null) {
    ekran = (
      <Nachalo
        onNachat={() => nachatSnachala(null)}
        zanyato={nachat.isPending}
        oshibka={oshibka}
      />
    )
  } else if (dannye.id !== vRabote) {
    ekran = (
      <Vybor
        chernovik={dannye}
        onProdolzhit={() => {
          zadatPosleZapisi(false)
          zadatVRabote(dannye.id)
        }}
        onZanovo={() => nachatSnachala(dannye.id)}
        zanyato={nachat.isPending}
        oshibka={oshibka}
      />
    )
  } else {
    // Ключ — шаг: у каждого шага свой ввод, с черновика заново. Память
    // шагов — снаружи ключа: она помнит, какой шаг показан первым.
    ekran = (
      <PamyatShagovMastera fokusSrazu={posleZapisi}>
        <Master
          key={dannye.step}
          chernovik={dannye}
          raspoznavanie={raspoznavanie}
          otpravka={otpravka}
        />
      </PamyatShagovMastera>
    )
  }

  return (
    <section className="kartochka-razdel">
      <h1>Новый ингредиент</h1>
      {ekran}
    </section>
  )
}

function Nachalo({
  onNachat,
  zanyato,
  oshibka,
}: {
  onNachat: () => void
  zanyato: boolean
  oshibka: string | null
}) {
  return (
    <div className="kartochka">
      <p className="kartochka-poyasnenie">
        Карточка нового продукта для таблицы: поставщик, категория и название, затем фото
        этикетки — состав, КБЖУ и сроки распознаются с него. Всё сохраняется по ходу: если
        отвлечётесь, продолжите с того же места.
      </p>
      {oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {oshibka}
        </p>
      )}
      <div className="kartochka-knopki">
        <button type="button" className="kartochka-glavnaya" onClick={onNachat} disabled={zanyato}>
          Начать
        </button>
      </div>
    </div>
  )
}

/** Есть незаконченная карточка — повар сам решает, продолжить её или
 *  начать заново. Заново — только после подтверждения: одно касание не
 *  должно стирать работу и фото. */
function Vybor({
  chernovik,
  onProdolzhit,
  onZanovo,
  zanyato,
  oshibka,
}: {
  chernovik: Draft
  onProdolzhit: () => void
  onZanovo: () => void
  zanyato: boolean
  oshibka: string | null
}) {
  const [sprashivaem, sprosit] = useState(false)
  const net = useRef<HTMLButtonElement>(null)
  const zanovo = useRef<HTMLButtonElement>(null)
  const sprashivali = useRef(false)

  // Вопрос открылся — фокус на безопасном ответе: случайное «Готово» или
  // пробел не сотрёт работу. Закрылся — фокус обратно, а не в никуда.
  useEffect(() => {
    if (sprashivaem) {
      sprashivali.current = true
      net.current?.focus()
    } else if (sprashivali.current) {
      zanovo.current?.focus()
    }
  }, [sprashivaem])

  return (
    <div className="kartochka">
      <p>У вас есть незаконченная карточка:</p>
      <p className="kartochka-svodka">
        <b>{chernovik.name || 'без названия'}</b>
        {chernovik.supplier && ` · ${chernovik.supplier}`}
      </p>
      <p className="kartochka-nomer">
        {`Шаг ${SHAGI.indexOf(chernovik.step) + 1} из ${SHAGI.length} — ${NAZVANIYA_SHAGOV[chernovik.step]}`}
      </p>
      {oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {oshibka}
        </p>
      )}
      {sprashivaem ? (
        <div className="kartochka-podtverzhdenie">
          <p>
            Начать заново? Незаконченная карточка удалится вместе с её фото — вернуть её будет
            нельзя.
          </p>
          <div className="kartochka-knopki">
            <button type="button" ref={net} onClick={() => sprosit(false)}>
              Нет, оставить
            </button>
            <button
              type="button"
              className="kartochka-opasnaya"
              onClick={() => {
                sprosit(false)
                onZanovo()
              }}
            >
              Да, начать заново
            </button>
          </div>
        </div>
      ) : (
        <div className="kartochka-knopki">
          <button type="button" ref={zanovo} onClick={() => sprosit(true)} disabled={zanyato}>
            Начать заново
          </button>
          <button
            type="button"
            className="kartochka-glavnaya"
            onClick={onProdolzhit}
            disabled={zanyato}
          >
            Продолжить
          </button>
        </div>
      )}
    </div>
  )
}

function Master({
  chernovik,
  raspoznavanie,
  otpravka,
}: {
  chernovik: Draft
  raspoznavanie: Raspoznavanie
  otpravka: Otpravka
}) {
  switch (chernovik.step) {
    case 'supplier':
      return <ShagPostavshchik chernovik={chernovik} />
    case 'category':
      return <ShagKategoriya chernovik={chernovik} />
    case 'name':
      return <ShagNazvanie chernovik={chernovik} />
    case 'label':
      return <ShagEtiketka chernovik={chernovik} raspoznavanie={raspoznavanie} />
    case 'review':
      return <ShagProverka chernovik={chernovik} raspoznavanie={raspoznavanie} />
    case 'approval':
      return <ShagSoglasovan chernovik={chernovik} />
    case 'photos':
      return <ShagFoto chernovik={chernovik} />
    case 'description':
      return <ShagOpisanie chernovik={chernovik} />
    case 'summary':
      return <ShagItog chernovik={chernovik} otpravka={otpravka} />
  }
}
