import { useMemo } from 'react'

import { primenit } from '../domain/tablitsa'
import { DataTable, type Column } from './DataTable'
import { PanelTablitsy } from './PanelTablitsy'
import { useTablitsaVAdrese } from './useTablitsaVAdrese'
import { ZagolovokKolonki } from './ZagolovokKolonki'

type Props<T> = {
  /** Полный список — в порядке ответа сервера: к нему возвращает
      выключенная сортировка. */
  stroki: readonly T[]
  /** Константа модуля экрана, а не массив, собранный при отрисовке: от неё
      зависят и состояние из адреса, и пересчёт таблицы — новый массив на
      каждую отрисовку пересчитывал бы всё на каждую отрисовку. */
  kolonki: Column<T>[]
  /** Поля, по которым ищет поиск, — название и id. Лучше тоже константа
      модуля: пересчёт зависит и от неё. */
  poiskPo: (r: T) => readonly string[]
  rowKey: (r: T) => string
  rowClass?: (r: T) => string | undefined
  /** Открыть строку. `adresSpiska` — `?…` или `''`: вид списка с поиском,
      ещё не ушедшим в адрес, — для возврата из карточки. */
  onOpen?: (r: T, adresSpiska: string) => void
  /** Текст экрана, когда строк нет вовсе. Когда их отсеяли поиск и
      фильтры, — «Ничего не найдено». */
  empty: string
}

/**
 * Таблица экрана со всем управлением: сортировка, фильтры и поиск живут в
 * адресе (`useTablitsaVAdrese`), строки и группы галочек считает
 * `primenit`, над таблицей — панель, в шапке — кнопки колонок. Экрану
 * остаётся описать колонки и дать строки.
 */
export function TablitsaSFiltrami<T>({ stroki, kolonki, poiskPo, rowKey, rowClass, onOpen, empty }: Props<T>) {
  const tablitsa = useTablitsaVAdrese(kolonki)
  const { sostoyanie } = tablitsa
  const itog = useMemo(() => primenit(stroki, kolonki, sostoyanie, poiskPo), [stroki, kolonki, sostoyanie, poiskPo])

  return (
    <>
      <PanelTablitsy
        kolonki={kolonki}
        vvod={tablitsa.vvod}
        onVvod={tablitsa.zadatVvod}
        naideno={itog.stroki.length}
        vsego={itog.vsego}
        sortirovka={sostoyanie.sortirovka}
        onSortirovka={tablitsa.zadatSortirovku}
        gruppy={itog.gruppy}
        onVybor={tablitsa.zadatVybor}
        onSbrositVsyo={tablitsa.sbrositVsyo}
      />
      <DataTable
        columns={kolonki}
        rows={itog.stroki}
        rowKey={rowKey}
        rowClass={rowClass}
        // Поиск, набранный только что, уходит в адрес через 300 мс, а
        // открыть строку шеф может и раньше. Поэтому перед уходом он
        // записывается в запись истории списка — к ней ведёт «назад»
        // браузера (уход со страницы таймер гасит), — а карточка получает
        // адрес списка из состояния, а не из location.search. Запись —
        // здесь, до перехода, а не при размонтировании: тогда в адресе уже
        // карточка, и замена легла бы поверх неё.
        onOpen={
          onOpen &&
          ((r) => {
            tablitsa.zapisatSeychas()
            onOpen(r, tablitsa.adresSeychas())
          })
        }
        // Пустой справочник — не «не найдено»: искать было не в чем, даже
        // если поиск набран.
        empty={itog.vsego === 0 ? empty : 'Ничего не найдено'}
        sortirovka={sostoyanie.sortirovka}
        zagolovok={(k) => (
          <ZagolovokKolonki
            kolonka={k}
            sortirovka={sostoyanie.sortirovka}
            onSort={tablitsa.pereklyuchitSortirovku}
            gruppa={itog.gruppy.find((g) => g.kolonka === k.key)}
            onVybor={tablitsa.zadatVybor}
          />
        )}
      />
    </>
  )
}
