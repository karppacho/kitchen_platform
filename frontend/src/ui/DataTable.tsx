import { useId, useState, type ReactNode } from 'react'

import './table.css'
import { useWide } from './useWide'

export type Column<T> = {
  key: string
  title: string
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
}

/**
 * Одна реализация на обе ширины.
 *
 * Два дерева разметки разъехались бы на первой же правке, и узкий вариант
 * тихо отстал бы от широкого — а он основной: боты были целиком
 * телефонными, и это надо было вернуть.
 */
export function DataTable<T>(props: Props<T>) {
  const { rows, empty } = props
  const wide = useWide()

  if (rows.length === 0) {
    return <p className="pusto">{empty}</p>
  }
  return wide ? <Shirokaya {...props} /> : <Uzkiy {...props} />
}

function Shirokaya<T>({ columns, rows, rowKey, rowClass, onOpen }: Props<T>) {
  return (
    <div className="tablitsa-obolochka">
      <table className="tablitsa">
        <thead>
          <tr>
            {columns.map((k) => (
              <th key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
                {k.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
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
  // Общий id между кнопкой-раскрытием и блоком подробностей — программе
  // чтения с экрана есть на что сослаться через aria-controls.
  const podrobnoId = useId()

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
          aria-controls={podrobnoId}
          onClick={() => raskryt(!raskryto)}
        >
          {raskryto ? 'Свернуть' : 'Подробнее'}
        </button>
      )}
      {raskryto && (
        <dl className="spisok-podrobno" id={podrobnoId}>
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
