import { Fragment, useState, type ReactNode } from 'react'

import type { Column } from './DataTable'
import { ShevronIcon } from './Icons'

type Props<T> = {
  columns: Column<T>[]
  row: T
  klass?: string | undefined
  onOpen?: ((row: T) => void) | undefined
}

// Пустая часть подписи — не рисуется и точки после себя не оставляет.
function pusto(chast: ReactNode): boolean {
  return chast === null || chast === undefined || chast === '' || chast === false
}

/**
 * Строка списка на телефоне. Слева заголовок и подпись мелким через « · »,
 * справа — значения столбиком с подписями, выровненные по правому краю:
 * у каждого значения своя строка, а не общая сетка на всю строку списка,
 * иначе число с замечанием растягивало бы колонку остальных.
 * Строка целиком — кнопка: открывает карточку (`onOpen`, шеврон вправо) или
 * раскрывает подробности (шеврон вниз/вверх, `aria-expanded`). Отдельной
 * ссылки «Подробнее» нет — решение 30.09.2026.
 */
export function StrokaSpiska<T>({ columns, row, klass, onOpen }: Props<T>) {
  const [raskryto, raskryt] = useState(false)

  const zagolovok = columns.find((k) => k.uzkiy === 'zagolovok')
  const podpisi = columns
    .filter((k) => k.uzkiy === 'podpis')
    .map((k) => (k.renderUzkiy ?? k.render)(row))
    .filter((chast) => !pusto(chast))
  const znacheniya = columns.filter((k) => k.uzkiy === 'znachenie')
  // Открываемая строка подробностей не показывает: всё остальное — в карточке.
  const podrobno = onOpen ? [] : columns.filter((k) => k.uzkiy === 'podrobno')
  const raskryvaemaya = podrobno.length > 0

  const telo = (
    <>
      <span className="stroka-levo">
        <span className="stroka-zagolovok">{zagolovok ? (zagolovok.renderUzkiy ?? zagolovok.render)(row) : null}</span>
        {podpisi.length > 0 && (
          <span className="stroka-podpis">
            {podpisi.map((chast, nomer) => (
              <Fragment key={nomer}>
                {nomer > 0 && ' · '}
                {chast}
              </Fragment>
            ))}
          </span>
        )}
      </span>
      {znacheniya.length > 0 && (
        <span className="stroka-pravo">
          {znacheniya.map((k) => {
            const podpis = k.nazvanieUzkoe ?? k.title
            return (
              <span key={k.key} className="stroka-znachenie">
                {podpis !== '' && <span className="stroka-znachenie-podpis">{podpis}</span>}
                {(k.renderUzkiy ?? k.render)(row)}
              </span>
            )
          })}
        </span>
      )}
      {(onOpen || raskryvaemaya) && (
        <ShevronIcon className="stroka-shevron" napravlenie={onOpen ? 'vpravo' : raskryto ? 'vverh' : 'vniz'} />
      )}
    </>
  )

  return (
    <li className={klass ? `stroka ${klass}` : 'stroka'}>
      {onOpen ? (
        <button type="button" className="stroka-telo" onClick={() => onOpen(row)}>
          {telo}
        </button>
      ) : raskryvaemaya ? (
        <button type="button" className="stroka-telo" aria-expanded={raskryto} onClick={() => raskryt(!raskryto)}>
          {telo}
        </button>
      ) : (
        <div className="stroka-telo">{telo}</div>
      )}
      {raskryto && (
        <dl className="stroka-podrobno">
          {podrobno.map((k) => (
            <div key={k.key}>
              <dt>{k.title}</dt>
              <dd>{(k.renderUzkiy ?? k.render)(row)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
