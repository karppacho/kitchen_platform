import { useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type RefObject } from 'react'

import { kodSortirovki, poiskIliPusto, razobratKodSortirovki } from '../domain/adres'
import { variantySortirovki, type GruppaFiltra, type OpisanieKolonki, type Sortirovka } from '../domain/tablitsa'
import { Fishki } from './Fishki'
import { Galochki } from './Galochki'
import './panelTablitsy.css'
import { useWide } from './useWide'

type Props<T> = {
  /** Колонки таблицы — из них пункты выбора «Сортировка» на узком экране. */
  kolonki: readonly OpisanieKolonki<T>[]
  /** Текст в поле поиска как есть — `vvod` у `useTablitsaVAdrese`. */
  vvod: string
  onVvod: (vvod: string) => void
  /** Сколько строк осталось после поиска и фильтров. */
  naideno: number
  /** Сколько строк всего. */
  vsego: number
  sortirovka: Sortirovka | null
  /** Выбор «Сортировка» — как `zadatSortirovku` у `useTablitsaVAdrese`. */
  onSortirovka: (s: Sortirovka | null) => void
  gruppy: readonly GruppaFiltra[]
  /** Новый выбор колонки целиком — как `zadatVybor` у `useTablitsaVAdrese`. */
  onVybor: (kolonka: string, kody: string[]) => void
  onSbrositVsyo: () => void
}

/**
 * Панель над таблицей: поиск, «Найдено: N из M», фишки включённых фильтров
 * и «Сбросить всё». На узком экране заголовков нет — там же выбор
 * «Сортировка» и «Фильтры» со всеми группами галочек.
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
  // заново. На узком — «Фильтры»: фокус на поле ввода из обработчика
  // касания открыл бы экранную клавиатуру на полэкрана (Chrome на Android
  // фокусирует кнопку при касании, и сюда доходит).
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
        {/* Одной строкой и целиком: программа чтения с экрана зачитывает
            изменение живой области, и «5» без «из 130» ничего бы не сказало. */}
        <span className="panel-tablitsy-naideno" aria-live="polite" aria-atomic="true">
          {`Найдено: ${naideno} из ${vsego}`}
        </span>
      </div>
      {!wide && (
        <UzkoeUpravlenie
          knopka={knopkaFiltrov}
          kolonki={kolonki}
          sortirovka={sortirovka}
          onSortirovka={onSortirovka}
          gruppy={gruppy}
          onVybor={onVybor}
        />
      )}
      {/* Фишки — под «Фильтрами», а не над ними: ряд, появившийся с первой
          галочкой, сдвинул бы открытую панель из-под пальца. */}
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

type SvoystvaUzkogo<T> = Pick<Props<T>, 'kolonki' | 'sortirovka' | 'onSortirovka' | 'gruppy' | 'onVybor'> & {
  /** Кнопка «Фильтры» — у панели: на неё же уходит фокус с исчезнувших
      фишек и «Сбросить всё». */
  knopka: RefObject<HTMLButtonElement>
}

/**
 * Сортировка и фильтры на узком экране. «Фильтры» раскрывают панель в
 * потоке страницы, а не поверх: на телефоне всплывающей панели негде
 * встать, а группы галочек — те же, что под значками шапки.
 */
function UzkoeUpravlenie<T>({ knopka, kolonki, sortirovka, onSortirovka, gruppy, onVybor }: SvoystvaUzkogo<T>) {
  const [otkryty, otkryt] = useState(false)
  const idPaneli = useId()
  const punkty = useMemo(() => variantySortirovki(kolonki), [kolonki])
  const vklyucheno = gruppy.filter((g) => g.aktivna).length

  function zakryt() {
    otkryt(false)
    // Фокус был в панели — она исчезнет, и он упал бы на body.
    knopka.current?.focus()
  }

  function klavisha(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    zakryt()
  }

  return (
    <>
      <div className="panel-tablitsy-ryad">
        <select
          className="panel-tablitsy-pole panel-tablitsy-sortirovka"
          aria-label="Сортировка"
          value={sortirovka ? kodSortirovki(sortirovka) : ''}
          onChange={(event) => onSortirovka(razobratKodSortirovki(event.target.value))}
        >
          <option value="">Без сортировки</option>
          {punkty.map((p) => (
            <option key={p.kod} value={p.kod}>
              {p.podpis}
            </option>
          ))}
        </select>
        <button
          ref={knopka}
          type="button"
          className="panel-tablitsy-filtry"
          aria-expanded={otkryty}
          aria-controls={otkryty ? idPaneli : undefined}
          onClick={() => otkryt(!otkryty)}
        >
          {vklyucheno > 0 ? `Фильтры (${vklyucheno})` : 'Фильтры'}
        </button>
      </div>
      {/* Закрытая — убрана из документа, а не спрятана: в галочках те же
          слова, что в ячейках («Соус»), и поиск текста по странице — у
          тестов экранов тоже — находил бы их дважды. */}
      {otkryty && (
        <div id={idPaneli} role="group" aria-label="Фильтры" className="panel-tablitsy-panel" onKeyDown={klavisha}>
          {gruppy.map((g) => (
            <Galochki key={g.kolonka} gruppa={g} onVybor={onVybor} />
          ))}
          <div className="panel-tablitsy-niz">
            <button type="button" className="panel-tablitsy-gotovo" onClick={zakryt}>
              Готово
            </button>
          </div>
        </div>
      )}
    </>
  )
}
