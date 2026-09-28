import { useLayoutEffect, useRef } from 'react'

import type { GruppaFiltra } from '../domain/tablitsa'
import './galochki.css'

type Props = {
  gruppa: GruppaFiltra
  /** Новый выбор колонки целиком — как `zadatVybor` у `useTablitsaVAdrese`. */
  onVybor: (kolonka: string, kody: string[]) => void
}

/**
 * Галочки одной колонки — один компонент на обе ширины: в панели под
 * значком фильтра на широком экране и во встроенных «Фильтрах» на узком.
 * Рядом со значением — сколько у него строк среди прошедших поиск и
 * фильтры других колонок (правила — в `domain/tablitsa.ts`).
 */
export function Galochki({ gruppa, onVybor }: Props) {
  const pole = useRef<HTMLFieldSetElement>(null)
  // Место тронутой галочки (у «Сбросить» — 0), пока правка не вернулась
  // новой группой.
  const pravka = useRef<number | null>(null)

  // Правка из группы может убрать элемент с фокусом: снятая галочка
  // значения без строк пропадает из списка, «Сбросить» — вместе с
  // активностью группы, а со сбросом могут уйти и все значения. Фокус упал
  // бы на body — вон из панели и её ловушки Tab. Ставим его на галочку,
  // вставшую на то же место (или на последнюю), а если галочек не осталось
  // — на саму группу. После перерисовки, а не в обработчике: что исчезнет,
  // видно только по новой группе.
  useLayoutEffect(() => {
    const mesto = pravka.current
    const gruppaDom = pole.current
    if (mesto === null || gruppaDom === null) return
    pravka.current = null
    if (gruppaDom.contains(document.activeElement)) return
    const galochki = gruppaDom.querySelectorAll('input')
    const tsel = galochki[Math.min(mesto, galochki.length - 1)] ?? gruppaDom
    tsel.focus()
  }, [gruppa])

  function pereklyuchit(kod: string, mesto: number) {
    pravka.current = mesto
    // Остальные отметки — как были: внутри колонки значения складываются.
    const kody = gruppa.varianty.filter((v) => (v.kod === kod ? !v.vybran : v.vybran)).map((v) => v.kod)
    onVybor(gruppa.kolonka, kody)
  }

  return (
    // tabIndex -1 — только чтобы группа могла принять фокус, когда галочек
    // в ней не осталось; в порядок Tab она не встаёт.
    <fieldset ref={pole} className="galochki" tabIndex={-1}>
      <legend>{gruppa.zagolovok}</legend>
      {gruppa.varianty.length === 0 && <p className="galochki-net">Нет значений</p>}
      {gruppa.varianty.map((v, mesto) => (
        <label key={v.kod} className="galochki-variant">
          <input type="checkbox" checked={v.vybran} onChange={() => pereklyuchit(v.kod, mesto)} />
          <span>
            <span className={v.kod === '' ? 'galochki-pusto' : undefined}>{v.podpis}</span>
            <span className="galochki-schyot"> · {v.schyot}</span>
          </span>
        </label>
      ))}
      {gruppa.aktivna && (
        <button
          type="button"
          className="galochki-sbros"
          onClick={() => {
            pravka.current = 0
            onVybor(gruppa.kolonka, [])
          }}
        >
          Сбросить
        </button>
      )}
    </fieldset>
  )
}
