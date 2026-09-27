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
  const posleSbrosa = useRef(false)

  // «Сбросить» пропадает вместе с активностью группы, а фокус был на нём:
  // без этого он упал бы на body, вон из панели. Ставим после перерисовки,
  // а не сразу: вместе со сбросом уходят отмеченные значения без строк, и
  // первая галочка до сброса могла исчезнуть.
  useLayoutEffect(() => {
    if (!posleSbrosa.current || gruppa.aktivna) return
    posleSbrosa.current = false
    pole.current?.querySelector('input')?.focus()
  }, [gruppa.aktivna])

  function pereklyuchit(kod: string) {
    // Остальные отметки — как были: внутри колонки значения складываются.
    const kody = gruppa.varianty.filter((v) => (v.kod === kod ? !v.vybran : v.vybran)).map((v) => v.kod)
    onVybor(gruppa.kolonka, kody)
  }

  return (
    <fieldset ref={pole} className="galochki">
      <legend>{gruppa.zagolovok}</legend>
      {gruppa.varianty.length === 0 && <p className="galochki-net">Нет значений</p>}
      {gruppa.varianty.map((v) => (
        <label key={v.kod} className="galochki-variant">
          <input type="checkbox" checked={v.vybran} onChange={() => pereklyuchit(v.kod)} />
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
            posleSbrosa.current = true
            onVybor(gruppa.kolonka, [])
          }}
        >
          Сбросить
        </button>
      )}
    </fieldset>
  )
}
