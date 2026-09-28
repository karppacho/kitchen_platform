import { useState, type ReactNode } from 'react'

import type { OpisanieKolonki, Sortirovka } from '../domain/tablitsa'
import './table.css'
import { useWide } from './useWide'

/** Колонка экрана: что сортировать и фильтровать (domain) и как рисовать. */
export type Column<T> = OpisanieKolonki<T> & {
  align?: 'left' | 'right'
  /** always — видно и на телефоне; wide — только на широком и по тапу. */
  priority: 'always' | 'wide'
  render: (row: T) => ReactNode
}

type Props<T> = {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty: string
  rowClass?: (row: T) => string | undefined
  onOpen?: (row: T) => void
  /** Текущая сортировка — ставит `aria-sort` на заголовок её колонки. */
  sortirovka?: Sortirovka | null
  /** Содержимое заголовка вместо `title` — кнопки сортировки и фильтра. */
  zagolovok?: (k: Column<T>) => ReactNode
}

/**
 * Одна реализация на обе ширины.
 *
 * Два дерева разметки разъехались бы на первой же правке, и узкий вариант
 * тихо отстал бы от широкого — а он основной: боты были целиком
 * телефонными, и это надо было вернуть.
 */
export function DataTable<T>(props: Props<T>) {
  const wide = useWide()

  // На широком пустой результат — та же таблица с шапкой: в шапке
  // сортировка и фильтры, и фильтр, отсеявший всё, снимают там же. На
  // узком шапки нет, управление — над списком, так что хватает слов.
  if (wide) return <Shirokaya {...props} />
  if (props.rows.length === 0) return <p className="pusto">{props.empty}</p>
  return <Uzkiy {...props} />
}

function Shirokaya<T>({ columns, rows, rowKey, empty, rowClass, onOpen, sortirovka, zagolovok }: Props<T>) {
  return (
    <div className="tablitsa-obolochka">
      <table className="tablitsa">
        <thead>
          <tr>
            {columns.map((k) => (
              <th
                key={k.key}
                className={k.align === 'right' ? 'vpravo' : undefined}
                aria-sort={ariaSort(k.key, sortirovka)}
                // Имя заголовка звучит на каждом переходе по ячейкам колонки.
                // Из содержимого с кнопками оно вышло бы «Категория Фильтр:
                // Категория, выбрано 2» — оставляем название, у кнопок внутри
                // свои имена.
                aria-label={zagolovok ? k.title : undefined}
              >
                {zagolovok ? zagolovok(k) : k.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr className="stroka-pusto">
              <td colSpan={columns.length} className="pusto">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={rowClass?.(row)}
              onClick={onOpen ? () => onOpen(row) : undefined}
              tabIndex={onOpen ? 0 : undefined}
              onKeyDown={
                onOpen
                  ? (event) => {
                      if (event.key === 'Enter') onOpen(row)
                    }
                  : undefined
              }
            >
              {columns.map((k) => (
                <td key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
                  {k.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Только у отсортированной колонки: `none` у остальных программа чтения
// с экрана зачитывала бы у каждого заголовка, а порядок задаёт одна.
function ariaSort(kolonka: string, s: Sortirovka | null | undefined): 'ascending' | 'descending' | undefined {
  if (s?.kolonka !== kolonka) return undefined
  return s.napravlenie === 'vozr' ? 'ascending' : 'descending'
}

function Uzkiy<T>({ columns, rows, rowKey, rowClass, onOpen }: Props<T>) {
  const glavnye = columns.filter((k) => k.priority === 'always')
  const ostalnye = columns.filter((k) => k.priority === 'wide')

  return (
    <ul className="spisok">
      {rows.map((row) => (
        <Kartochka
          key={rowKey(row)}
          row={row}
          glavnye={glavnye}
          ostalnye={ostalnye}
          klass={rowClass?.(row)}
          onOpen={onOpen}
        />
      ))}
    </ul>
  )
}

function Kartochka<T>({
  row,
  glavnye,
  ostalnye,
  klass,
  onOpen,
}: {
  row: T
  glavnye: Column<T>[]
  ostalnye: Column<T>[]
  klass?: string
  onOpen?: (row: T) => void
}) {
  const [raskryto, raskryt] = useState(false)

  const glavnoe = glavnye.map((k) => (
    <span key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
      {k.render(row)}
    </span>
  ))

  return (
    <li className={klass}>
      {onOpen ? (
        // Основной экран телефона — карточка должна открываться и с
        // клавиатуры. Нативная <button> даёт это бесплатно: Enter и
        // Space срабатывают сами, без самодельного onKeyDown.
        <button type="button" className="spisok-glavnoe" onClick={() => onOpen(row)}>
          {glavnoe}
        </button>
      ) : (
        <div className="spisok-glavnoe">{glavnoe}</div>
      )}
      {ostalnye.length > 0 && (
        <button
          type="button"
          className="raskryt"
          aria-expanded={raskryto}
          onClick={() => raskryt(!raskryto)}
        >
          {raskryto ? 'Свернуть' : 'Подробнее'}
        </button>
      )}
      {raskryto && (
        <dl className="spisok-podrobno">
          {ostalnye.map((k) => (
            <div key={k.key}>
              <dt>{k.title}</dt>
              <dd>{k.render(row)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
