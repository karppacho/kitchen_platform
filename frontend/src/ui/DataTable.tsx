import type { ReactNode } from 'react'

import type { OpisanieKolonki, Sortirovka } from '../domain/tablitsa'
import { StrokaSpiska } from './StrokaSpiska'
import './table.css'
import { useWide } from './useWide'

/**
 * Роль колонки в строке списка на телефоне. На широком экране колонка — это
 * колонка; на телефоне из колонок собирается строка (`StrokaSpiska`), и
 * каждой нужно место в ней, а не признак «показывать или нет».
 */
export type RolUzkogo = 'zagolovok' | 'podpis' | 'znachenie' | 'podrobno' | 'skryto'

/** Колонка экрана: что сортировать и фильтровать (domain) и как рисовать. */
export type Column<T> = OpisanieKolonki<T> & {
  align?: 'left' | 'right'
  /**
   * zagolovok — заголовок строки; podpis — часть подписи мелким через « · »;
   * znachenie — значение справа с подписью; podrobno — только по раскрытию
   * строки; skryto — на телефоне не показывается вовсе.
   */
  uzkiy: RolUzkogo
  /** Подпись значения на телефоне вместо `title`; '' — без подписи. */
  nazvanieUzkoe?: string
  render: (row: T) => ReactNode
  /**
   * Как рисовать на телефоне, если иначе, чем в таблице. У части подписи
   * null или '' — часть не показывается и точки после себя не оставляет.
   */
  renderUzkiy?: (row: T) => ReactNode
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
  return (
    <ul className="spisok">
      {rows.map((row) => (
        <StrokaSpiska key={rowKey(row)} columns={columns} row={row} klass={rowClass?.(row)} onOpen={onOpen} />
      ))}
    </ul>
  )
}
