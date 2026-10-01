import type { FormEvent, ReactNode } from 'react'

import { NAZVANIYA_SHAGOV, SHAGI, usePravkaChernovika } from '../../api/kartochki'
import type { Draft, DraftPatch, Shag } from '../../api/types'
import './kartochka.css'

/** Заголовок шага подписывает его главное поле: отдельная подпись над полем
 *  повторяла бы заголовок слово в слово. Мастер на странице один. */
export const ZAGOLOVOK_SHAGA = 'kartochka-zagolovok-shaga'

export type UpravlenieShagom = {
  shag: Shag
  /** Сохранить поля шага и перейти дальше — одной правкой: вернувшись к
   *  черновику, повар продолжит ровно с того шага, где остановился. */
  dalee: (polya: DraftPatch, kuda?: Shag) => void
  /** На первом шаге назад некуда. */
  nazad: (() => void) | undefined
  /** Правка ещё идёт — кнопки ждут: двойное касание не шлёт её дважды. */
  zanyato: boolean
  oshibka: string | null
}

export function useShag(chernovik: Draft): UpravlenieShagom {
  const pravka = usePravkaChernovika()
  const nomer = SHAGI.indexOf(chernovik.step)
  const pereiti = (step: Shag, polya: DraftPatch = {}) =>
    pravka.mutate({ id: chernovik.id, pravka: { ...polya, step } })
  const prezhniy = SHAGI[nomer - 1]

  return {
    shag: chernovik.step,
    dalee: (polya, kuda) => pereiti(kuda ?? SHAGI[nomer + 1] ?? chernovik.step, polya),
    nazad: prezhniy === undefined ? undefined : () => pereiti(prezhniy),
    zanyato: pravka.isPending,
    oshibka:
      pravka.error === null
        ? null
        : pravka.error instanceof Error
          ? pravka.error.message
          : 'Не удалось сохранить',
  }
}

/**
 * Общая рамка шага: «Шаг N из 9», заголовок, отказ сервера и кнопки.
 *
 * Форма, а не просто блок: «Готово» на клавиатуре телефона отправляет её —
 * это то же «Далее», и действует оно только там, где «Далее» доступна.
 */
export function RamkaShaga({
  upravlenie,
  mozhnoDalee = false,
  onDalee,
  children,
}: {
  upravlenie: UpravlenieShagom
  mozhnoDalee?: boolean
  /** Нет — у шага нет «Далее» (шаг ещё не готов). */
  onDalee?: () => void
  children: ReactNode
}) {
  const { shag, nazad, zanyato, oshibka } = upravlenie

  function otpravit(sobytie: FormEvent) {
    sobytie.preventDefault()
    if (onDalee && mozhnoDalee && !zanyato) onDalee()
  }

  return (
    <form className="kartochka" onSubmit={otpravit}>
      <p className="kartochka-nomer">{`Шаг ${SHAGI.indexOf(shag) + 1} из ${SHAGI.length}`}</p>
      <h2 id={ZAGOLOVOK_SHAGA}>{NAZVANIYA_SHAGOV[shag]}</h2>
      {children}
      {oshibka !== null && (
        <p className="kartochka-oshibka" role="alert">
          {oshibka}
        </p>
      )}
      <div className="kartochka-knopki">
        {nazad && (
          <button type="button" onClick={nazad} disabled={zanyato}>
            Назад
          </button>
        )}
        {onDalee && (
          <button
            type="submit"
            className="kartochka-glavnaya"
            disabled={!mozhnoDalee || zanyato}
          >
            Далее
          </button>
        )}
      </div>
    </form>
  )
}
