import { useState } from 'react'

import { NAZVANIYA_SHAGOV, SHAGI, useChernovik, useNachatChernovik } from '../api/kartochki'
import type { Draft } from '../api/types'
import { Sostoyanie } from '../ui/Sostoyanie'
import { RamkaShaga, useShag } from './kartochka/RamkaShaga'
import { ShagKategoriya } from './kartochka/ShagKategoriya'
import { ShagNazvanie } from './kartochka/ShagNazvanie'
import { ShagPostavshchik } from './kartochka/ShagPostavshchik'
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
  // id черновика, с которым повар работает: начал его здесь или выбрал
  // «Продолжить». Черновик сменился (завели в другой вкладке) — снова вопрос.
  const [vRabote, zadatVRabote] = useState<string | null>(null)
  const nachat = useNachatChernovik((novyi) => zadatVRabote(novyi.id))
  const oshibka = nachat.error instanceof Error ? nachat.error.message : null

  const dannye = chernovik.data
  let ekran
  if (dannye === undefined) {
    ekran = <Sostoyanie query={chernovik} />
  } else if (dannye === null) {
    ekran = (
      <Nachalo
        onNachat={() => nachat.mutate(null)}
        zanyato={nachat.isPending}
        oshibka={oshibka}
      />
    )
  } else if (dannye.id !== vRabote) {
    ekran = (
      <Vybor
        chernovik={dannye}
        onProdolzhit={() => zadatVRabote(dannye.id)}
        onZanovo={() => nachat.mutate(dannye.id)}
        zanyato={nachat.isPending}
        oshibka={oshibka}
      />
    )
  } else {
    // Ключ — шаг: у каждого шага свой ввод, с черновика заново.
    ekran = <Master key={dannye.step} chernovik={dannye} />
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
            <button type="button" onClick={() => sprosit(false)}>
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
          <button type="button" onClick={() => sprosit(true)} disabled={zanyato}>
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

function Master({ chernovik }: { chernovik: Draft }) {
  switch (chernovik.step) {
    case 'supplier':
      return <ShagPostavshchik chernovik={chernovik} />
    case 'category':
      return <ShagKategoriya chernovik={chernovik} />
    case 'name':
      return <ShagNazvanie chernovik={chernovik} />
    default:
      return <ShagPozzhe chernovik={chernovik} />
  }
}

/** Шаги с этикетки и дальше появятся следующими задачами этапа. До них —
 *  честно: шаг не готов, заполненное сохранено, назад можно. */
function ShagPozzhe({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik)
  return (
    <RamkaShaga upravlenie={upravlenie}>
      <p className="kartochka-poyasnenie">
        Этот шаг ещё не готов. Всё, что вы заполнили, сохранено.
      </p>
    </RamkaShaga>
  )
}
