import { useRef, type MouseEvent } from 'react'

import { poiskIliPusto } from '../domain/adres'
import type { GruppaFiltra, OpisanieKolonki, Sortirovka } from '../domain/tablitsa'
import { Fishki } from './Fishki'
import { PanelOtbora } from './PanelOtbora'
import './panelTablitsy.css'
import { useWide } from './useWide'

type Props<T> = {
  /** Колонки таблицы — из них переключатели сортировки в панели на телефоне. */
  kolonki: readonly OpisanieKolonki<T>[]
  /** Текст в поле поиска как есть — `vvod` у `useTablitsaVAdrese`. */
  vvod: string
  onVvod: (vvod: string) => void
  /** Сколько строк осталось после поиска и фильтров. */
  naideno: number
  /** Сколько строк всего. */
  vsego: number
  sortirovka: Sortirovka | null
  /** Выбор сортировки — как `zadatSortirovku` у `useTablitsaVAdrese`. */
  onSortirovka: (s: Sortirovka | null) => void
  gruppy: readonly GruppaFiltra[]
  /** Новый выбор колонки целиком — как `zadatVybor` у `useTablitsaVAdrese`. */
  onVybor: (kolonka: string, kody: string[]) => void
  onSbrositVsyo: () => void
}

/**
 * Панель над таблицей: поиск, «Найдено: N из M», фишки включённых фильтров
 * и «Сбросить всё». На телефоне заголовков с сортировкой и значками
 * фильтров нет — там под полем кнопка «Сортировка и фильтры», открывающая
 * панель снизу (`PanelOtbora`).
 */
export function PanelTablitsy<T>({
  kolonki,
  vvod,
  onVvod,
  naideno,
  vsego,
  sortirovka,
  onSortirovka,
  gruppy,
  onVybor,
  onSbrositVsyo,
}: Props<T>) {
  const wide = useWide()
  const pole = useRef<HTMLInputElement>(null)
  const knopkaFiltrov = useRef<HTMLButtonElement>(null)
  // Поиск из одних пробелов ничего не отбирает — и сбрасывать в нём нечего.
  const estOtbor = poiskIliPusto(vvod) !== '' || gruppy.some((g) => g.aktivna)

  // Куда фокус, когда исчезла кнопка отбора (последняя фишка, «Сбросить
  // всё»), а соседок не осталось. На широком — поиск: с него начинают
  // заново. На узком — «Сортировка и фильтры»: фокус на поле ввода из
  // обработчика касания открыл бы экранную клавиатуру на полэкрана (Chrome
  // на Android фокусирует кнопку при касании, и сюда доходит).
  function zapasnoyFokus() {
    if (wide) pole.current?.focus()
    else knopkaFiltrov.current?.focus()
  }

  function sbrosit(event: MouseEvent<HTMLButtonElement>) {
    // Кнопка пропадёт вместе с отбором. Как у фишек: фокуса на ней нет
    // (касание в iOS, щелчок в macOS) — терять нечего, не трогаем.
    if (event.currentTarget === document.activeElement) zapasnoyFokus()
    onSbrositVsyo()
  }

  // Одной строкой и целиком: программа чтения с экрана зачитывает
  // изменение живой области, и «5» без «из 130» ничего бы не сказало.
  // Узел один, место разное: на широком — рядом с полем, на телефоне — под
  // ним, в одном ряду с кнопкой отбора (поле там во всю строку).
  const naidenoDom = (
    <span className="panel-tablitsy-naideno" aria-live="polite" aria-atomic="true">
      {`Найдено: ${naideno} из ${vsego}`}
    </span>
  )

  return (
    <div className="panel-tablitsy">
      <div className="panel-tablitsy-ryad">
        <input
          ref={pole}
          type="search"
          className="panel-tablitsy-pole panel-tablitsy-poisk"
          aria-label="Поиск по названию или id"
          placeholder="Поиск по названию или id"
          value={vvod}
          onChange={(event) => onVvod(event.target.value)}
        />
        {wide && naidenoDom}
      </div>
      {!wide && (
        <div className="panel-tablitsy-ryad panel-tablitsy-ryad--otbor">
          {naidenoDom}
          <PanelOtbora
            knopka={knopkaFiltrov}
            kolonki={kolonki}
            naideno={naideno}
            sortirovka={sortirovka}
            onSortirovka={onSortirovka}
            gruppy={gruppy}
            onVybor={onVybor}
            onSbrositVsyo={onSbrositVsyo}
          />
        </div>
      )}
      {/* Фишки — последним рядом: он один появляется и исчезает вместе с
          отбором, и ниже него ничего нет — поле и кнопка отбора не прыгают. */}
      {estOtbor && (
        <div className="panel-tablitsy-ryad">
          <Fishki
            gruppy={gruppy}
            onSnyat={(kolonka) => onVybor(kolonka, [])}
            fokusDalshe={zapasnoyFokus}
          />
          <button type="button" className="panel-tablitsy-sbros" onClick={sbrosit}>
            Сбросить всё
          </button>
        </div>
      )}
    </div>
  )
}
