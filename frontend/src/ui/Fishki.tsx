import { useRef, type MouseEvent } from 'react'

import type { GruppaFiltra } from '../domain/tablitsa'
import { KrestikIcon } from './Icons'

type Props = {
  /** Все группы таблицы; фишки — у включённых. */
  gruppy: readonly GruppaFiltra[]
  /** Снять фильтр колонки целиком. */
  onSnyat: (kolonka: string) => void
  /** Куда деть фокус, когда снята последняя фишка: решает панель — на
      узком экране это «Фильтры», на широком поиск. */
  fokusDalshe: () => void
}

/**
 * Фишки над таблицей — по одной на колонку с включённым фильтром:
 * «Категория: Пицца, Соус». Видно, что сужает список, не открывая панелей
 * (на телефоне значков в шапке нет вовсе), и снять фильтр — одно нажатие.
 * Фишка целиком — кнопка: крестик в 14 px пальцем не попасть.
 */
export function Fishki({ gruppy, onSnyat, fokusDalshe }: Props) {
  const spisok = useRef<HTMLUListElement>(null)
  const vklyuchyonnye = gruppy.filter((g) => g.aktivna)
  if (vklyuchyonnye.length === 0) return null

  function snyat(event: MouseEvent<HTMLButtonElement>, kolonka: string, mesto: number) {
    // Снятая фишка исчезнет, и фокус с неё упал бы на body. Переносим его
    // сразу, пока соседки на месте: фишки — по ключу колонки, и соседки
    // переживут перерисовку. Сначала следующая, за последней — предыдущая:
    // фокус остаётся среди фишек, пока они есть. Фокуса на фишке нет
    // (касание в iOS, щелчок в macOS) — терять нечего, не трогаем.
    if (event.currentTarget === document.activeElement) {
      const knopki = spisok.current?.querySelectorAll('button')
      const sosedka = knopki?.[mesto + 1] ?? knopki?.[mesto - 1]
      if (sosedka) sosedka.focus()
      else fokusDalshe()
    }
    onSnyat(kolonka)
  }

  return (
    <ul ref={spisok} className="fishki" aria-label="Активные фильтры">
      {vklyuchyonnye.map((g, mesto) => {
        // Отмеченные — и те, у которых сейчас нет строк: их отметка действует.
        const tekst = `${g.zagolovok}: ${g.varianty
          .filter((v) => v.vybran)
          .map((v) => v.podpis)
          .join(', ')}`
        return (
          <li key={g.kolonka}>
            <button
              type="button"
              className="fishka"
              aria-label={`Снять фильтр «${tekst}»`}
              onClick={(event) => snyat(event, g.kolonka, mesto)}
            >
              <span className="fishka-tekst">{tekst}</span>
              <KrestikIcon className="fishka-krestik" />
            </button>
          </li>
        )
      })}
    </ul>
  )
}
