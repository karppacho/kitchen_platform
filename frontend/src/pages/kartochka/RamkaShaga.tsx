import {
  createContext,
  useContext,
  useEffect,
  useRef,
  type FormEvent,
  type MutableRefObject,
  type ReactNode,
} from 'react'

import { ApiError } from '../../api/client'
import { NAZVANIYA_SHAGOV, SHAGI, usePravkaChernovika } from '../../api/kartochki'
import type { Draft, DraftPatch, Shag } from '../../api/types'
import './kartochka.css'

/** Заголовок шага подписывает его главное поле: отдельная подпись над полем
 *  повторяла бы заголовок слово в слово. Мастер на странице один. */
export const ZAGOLOVOK_SHAGA = 'kartochka-zagolovok-shaga'

/** Сбой связи или сервера при сохранении: правка не дошла, повар повторит. */
const NE_SOKHRANILOS = 'Не удалось сохранить — проверьте связь и нажмите ещё раз'

/** id поля шага — по нему рамка ведёт фокус к полю, которому отказал сервер. */
export function idPolya(pole: string): string {
  return `kartochka-pole-${pole}`
}

function idOshibki(pole: string): string {
  return `kartochka-oshibka-${pole}`
}

/** Отказ сервера, относящийся к одному полю шага: «Белки: «abc» — не число». */
export type OshibkaPolya = { pole: string; tekst: string }

export type UpravlenieShagom = {
  shag: Shag
  /** Сохранить поля шага и перейти дальше — одной правкой: вернувшись к
   *  черновику, повар продолжит ровно с того шага, где остановился. */
  dalee: (polya: DraftPatch, kuda?: Shag) => void
  /** Назад — с тем, что набрано на шаге (уходит только изменённое). На
   *  первом шаге назад некуда. */
  nazad: ((polya?: DraftPatch) => void) | undefined
  /** Правка ещё идёт — кнопки ждут: двойное касание не шлёт её дважды. */
  zanyato: boolean
  /** Отказ, который не относится к полю шага, — внизу, над кнопками. */
  oshibka: string | null
  /** Отказ у поля шага — показывается у самого поля. */
  oshibkaPolya: OshibkaPolya | null
}

function tekstOtkaza(oshibka: unknown): string {
  // Обрыв, срок, 5xx — правка не дошла или сервер не смог: это не «не удалось
  // получить данные», а «не сохранилось, нажмите ещё раз».
  if (oshibka instanceof ApiError && (oshibka.status === 0 || oshibka.status >= 500)) {
    return NE_SOKHRANILOS
  }
  return oshibka instanceof Error ? oshibka.message : NE_SOKHRANILOS
}

/** У шага нет своих полей с отказом у поля. Постоянный — не новый массив
 *  на каждую отрисовку. */
const NET_POLEY: readonly string[] = []

/** Только то, что отличается от черновика: «Назад» не пишет нетронутое. */
function izmenennoe(chernovik: Draft, polya: DraftPatch): DraftPatch {
  return Object.fromEntries(
    Object.entries(polya).filter(([pole, znachenie]) => chernovik[pole as keyof Draft] !== znachenie),
  )
}

/**
 * Переход между шагами — правкой черновика.
 *
 * `svoiPolya` — поля, которые шаг показывает сам: отказ сервера по такому
 * полю виден у поля, и фокус ведёт к нему (оно может быть ниже края экрана
 * телефона). Передавать постоянный список — не новый массив на отрисовку.
 */
export function useShag(
  chernovik: Draft,
  svoiPolya: readonly string[] = NET_POLEY,
): UpravlenieShagom {
  const pravka = usePravkaChernovika()
  // Правка ушла — вторая ждёт. Не только серой кнопкой: два касания подряд
  // успевают раньше, чем экран перерисуется.
  const idyot = useRef(false)
  const nomer = SHAGI.indexOf(chernovik.step)
  const pereiti = (step: Shag, polya: DraftPatch = {}) => {
    if (idyot.current) return
    idyot.current = true
    pravka.mutate(
      { id: chernovik.id, pravka: { ...polya, step } },
      {
        onSettled: () => {
          idyot.current = false
        },
      },
    )
  }
  const prezhniy = SHAGI[nomer - 1]

  const otkaz = pravka.error
  const pole = otkaz instanceof ApiError ? otkaz.field : null
  const tekst = otkaz === null ? null : tekstOtkaza(otkaz)
  const uPolya = pole !== null && svoiPolya.includes(pole)

  useEffect(() => {
    if (otkaz instanceof ApiError && otkaz.field !== null && svoiPolya.includes(otkaz.field)) {
      document.getElementById(idPolya(otkaz.field))?.focus()
    }
  }, [otkaz, svoiPolya])

  return {
    shag: chernovik.step,
    dalee: (polya, kuda) => pereiti(kuda ?? SHAGI[nomer + 1] ?? chernovik.step, polya),
    nazad:
      prezhniy === undefined
        ? undefined
        : (polya = {}) => pereiti(prezhniy, izmenennoe(chernovik, polya)),
    zanyato: pravka.isPending,
    oshibka: uPolya ? null : tekst,
    oshibkaPolya: uPolya && tekst !== null ? { pole, tekst } : null,
  }
}

/** Свойства поля шага: id для фокуса и, при отказе, пометка и связь с
 *  текстом отказа. `opisanie` — свой id пояснения, если он у поля есть. */
export function svoistvaPolya(upravlenie: UpravlenieShagom, pole: string, opisanie?: string) {
  const otkaz = upravlenie.oshibkaPolya?.pole === pole
  const opisaniya = [opisanie, otkaz ? idOshibki(pole) : undefined].filter(Boolean).join(' ')
  return {
    id: idPolya(pole),
    'aria-invalid': otkaz ? true : undefined,
    'aria-describedby': opisaniya === '' ? undefined : opisaniya,
  }
}

/** Текст отказа под полем — если отказ у этого поля. */
export function OshibkaUPolya({ upravlenie, pole }: { upravlenie: UpravlenieShagom; pole: string }) {
  const otkaz = upravlenie.oshibkaPolya
  if (otkaz?.pole !== pole) return null
  return (
    <p id={idOshibki(pole)} className="kartochka-oshibka" role="alert">
      {otkaz.tekst}
    </p>
  )
}

type PamyatShagov = { pervyi: Shag | null; byloSmeny: boolean }

const Pamyat = createContext<MutableRefObject<PamyatShagov> | null>(null)

/**
 * Помнит, какой шаг мастер показал первым. При смене шага фокус уходит на
 * заголовок нового: кнопка, на которой он был, исчезла вместе с прежним
 * шагом, и читалка экрана замолчала бы. При первом показе фокус не трогаем.
 */
export function PamyatShagovMastera({ children }: { children: ReactNode }) {
  const pamyat = useRef<PamyatShagov>({ pervyi: null, byloSmeny: false })
  return <Pamyat.Provider value={pamyat}>{children}</Pamyat.Provider>
}

/**
 * Общая рамка шага: «Шаг N из 9», заголовок, отказ сервера и кнопки.
 *
 * Форма, а не просто блок: «Готово» на клавиатуре телефона отправляет её —
 * это то же «Далее», и действует оно только там, где «Далее» доступна.
 *
 * `polya` — что набрано на шаге сейчас: «Назад» уносит это с собой.
 *
 * `zhdyom` — шаг занят своим делом (фото готовится или загружается, идёт
 * отправка): «Назад» и «Далее» ждут. Ушедший шаг не дождался бы конца —
 * то, что должно было случиться после (распознавание), не случилось бы.
 */
export function RamkaShaga({
  upravlenie,
  mozhnoDalee = false,
  onDalee,
  tekstDalee = 'Далее',
  polya,
  zhdyom = false,
  children,
}: {
  upravlenie: UpravlenieShagom
  mozhnoDalee?: boolean
  /** Нет — у шага нет «Далее»: ответ на вопрос шага и есть переход. */
  onDalee?: () => void
  /** Подпись главной кнопки: «Пропустить», «Отправить в таблицу». */
  tekstDalee?: string
  polya?: DraftPatch
  zhdyom?: boolean
  children: ReactNode
}) {
  const { shag, nazad, oshibka } = upravlenie
  const zanyato = upravlenie.zanyato || zhdyom
  const pamyat = useContext(Pamyat)
  const zagolovok = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    const p = pamyat?.current
    if (!p) return
    if (p.pervyi === null) {
      p.pervyi = shag
      return
    }
    if (p.byloSmeny || p.pervyi !== shag) {
      p.byloSmeny = true
      zagolovok.current?.focus()
    }
  }, [pamyat, shag])

  function otpravit(sobytie: FormEvent) {
    sobytie.preventDefault()
    if (onDalee && mozhnoDalee && !zanyato) onDalee()
  }

  return (
    <form className="kartochka" onSubmit={otpravit}>
      <p className="kartochka-nomer">{`Шаг ${SHAGI.indexOf(shag) + 1} из ${SHAGI.length}`}</p>
      <h2 id={ZAGOLOVOK_SHAGA} ref={zagolovok} tabIndex={-1}>
        {NAZVANIYA_SHAGOV[shag]}
      </h2>
      {children}
      {oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {oshibka}
        </p>
      )}
      <div className="kartochka-knopki">
        {nazad && (
          <button type="button" onClick={() => nazad(polya)} disabled={zanyato}>
            Назад
          </button>
        )}
        {onDalee && (
          <button
            type="submit"
            className="kartochka-glavnaya"
            disabled={!mozhnoDalee || zanyato}
          >
            {tekstDalee}
          </button>
        )}
      </div>
    </form>
  )
}
