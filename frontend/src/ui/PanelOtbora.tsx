import { useCallback, useId, useMemo, useRef, useState, type MouseEvent, type RefObject } from 'react'

import { kodSortirovki, razobratKodSortirovki } from '../domain/adres'
import { variantySortirovki, type GruppaFiltra, type OpisanieKolonki, type Sortirovka } from '../domain/tablitsa'
import { Galochki } from './Galochki'
import { ShevronIcon } from './Icons'
import { ModalnayaPanel } from './ModalnayaPanel'

type Props<T> = {
  /** Кнопка «Сортировка и фильтры» — у панели над таблицей: на неё уходит
      фокус с исчезнувших фишек, на неё же возвращается при закрытии. */
  knopka: RefObject<HTMLButtonElement>
  kolonki: readonly OpisanieKolonki<T>[]
  naideno: number
  sortirovka: Sortirovka | null
  onSortirovka: (s: Sortirovka | null) => void
  gruppy: readonly GruppaFiltra[]
  onVybor: (kolonka: string, kody: string[]) => void
  onSbrositVsyo: () => void
}

/**
 * Сортировка и фильтры на телефоне — панель снизу поверх списка, а не в
 * потоке страницы: 25 галочек стеной уводили список за экран. Отбор
 * применяется сразу (состояние — в адресе, как и на компьютере), «Показать
 * N» только закрывает; N живой.
 */
export function PanelOtbora<T>({ knopka, kolonki, naideno, sortirovka, onSortirovka, gruppy, onVybor, onSbrositVsyo }: Props<T>) {
  const [otkryta, otkryt] = useState(false)
  const id = useId()
  const pokazat = useRef<HTMLButtonElement>(null)
  const punkty = useMemo(() => variantySortirovki(kolonki), [kolonki])
  const vklyucheno = gruppy.filter((g) => g.aktivna).length
  const zakryt = useCallback(() => otkryt(false), [])
  const tekushchiy = sortirovka ? kodSortirovki(sortirovka) : ''

  function sbrositVsyo(event: MouseEvent<HTMLButtonElement>) {
    // Кнопка пропадёт вместе с включёнными группами, и фокус с неё упал бы
    // на body — вон из панели: Escape до её обработчика не дошёл бы, а при
    // закрытии фокус не вернулся бы на кнопку «Сортировка и фильтры».
    // Переносим на «Показать N» — соседку, которая остаётся. Как у фишек:
    // фокуса на кнопке нет (касание в iOS, щелчок в macOS) — не трогаем.
    if (event.currentTarget === document.activeElement) pokazat.current?.focus()
    onSbrositVsyo()
  }

  return (
    <>
      <button
        ref={knopka}
        type="button"
        className="panel-otbora-knopka"
        aria-haspopup="dialog"
        aria-expanded={otkryta}
        aria-controls={otkryta ? id : undefined}
        onClick={() => otkryt(!otkryta)}
      >
        {vklyucheno > 0 ? `Сортировка и фильтры (${vklyucheno})` : 'Сортировка и фильтры'}
      </button>
      {otkryta && (
        <ModalnayaPanel
          id={id}
          storona="snizu"
          nazvanie="Сортировка и фильтры"
          onZakryt={zakryt}
          otkryvatel={knopka}
          niz={
            <>
              <button ref={pokazat} type="button" className="panel-otbora-pokazat" onClick={zakryt}>
                {`Показать ${naideno}`}
              </button>
              {vklyucheno > 0 && (
                <button type="button" className="panel-otbora-sbros" onClick={sbrositVsyo}>
                  Сбросить всё
                </button>
              )}
            </>
          }
        >
          {/* Живая область внутри диалога: «Найдено: N из M» снаружи лежит под
              inert, пока панель открыта, и программа чтения с экрана его
              изменений не зачитывает. */}
          <span className="vizualno-skryto" aria-live="polite" aria-atomic="true">
            {`Найдено ${naideno}`}
          </span>
          <fieldset className="panel-otbora-sortirovka">
            <legend>Сортировка</legend>
            <label className="panel-otbora-variant">
              <input type="radio" name={`${id}-sort`} value="" checked={tekushchiy === ''} onChange={() => onSortirovka(null)} />
              <span>Без сортировки</span>
            </label>
            {punkty.map((p) => (
              <label key={p.kod} className="panel-otbora-variant">
                <input
                  type="radio"
                  name={`${id}-sort`}
                  value={p.kod}
                  checked={tekushchiy === p.kod}
                  onChange={() => onSortirovka(razobratKodSortirovki(p.kod))}
                />
                <span>{p.podpis}</span>
              </label>
            ))}
          </fieldset>
          {gruppy.map((g) => (
            <GruppaOtbora key={g.kolonka} gruppa={g} onVybor={onVybor} />
          ))}
        </ModalnayaPanel>
      )}
    </>
  )
}

/** Группа галочек в сворачиваемом блоке: включённая при открытии раскрыта.
    Своё состояние, а не `open={aktivna}`: иначе свёрнутую руками группу
    перерисовка после каждой галочки раскрывала бы снова. */
function GruppaOtbora({ gruppa, onVybor }: { gruppa: GruppaFiltra; onVybor: (kolonka: string, kody: string[]) => void }) {
  const [raskryta, raskryt] = useState(gruppa.aktivna)
  const vybrano = gruppa.varianty.filter((v) => v.vybran).length
  return (
    <details className="panel-otbora-gruppa" open={raskryta} onToggle={(event) => raskryt(event.currentTarget.open)}>
      {/* Свой значок раскрытия: summary у нас flex, а маркер браузер рисует
          только у list-item; у раскрытой группы стрелка повёрнута (CSS). */}
      <summary>
        {vybrano > 0 ? `${gruppa.zagolovok} · ${vybrano}` : gruppa.zagolovok}
        <ShevronIcon className="panel-otbora-strelka" napravlenie="vniz" />
      </summary>
      <Galochki gruppa={gruppa} onVybor={onVybor} />
    </details>
  )
}
