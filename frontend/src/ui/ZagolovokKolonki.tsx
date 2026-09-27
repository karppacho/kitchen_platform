import { useCallback, useRef, useState } from 'react'

import type { GruppaFiltra, Sortirovka } from '../domain/tablitsa'
import type { Column } from './DataTable'
import { Galochki } from './Galochki'
import { SortirovkaIcon, VoronkaIcon } from './Icons'
import { Vsplyvashka } from './Vsplyvashka'

type Props<T> = {
  kolonka: Column<T>
  /** Сортировка таблицы: значок рисует направление, если она по этой колонке. */
  sortirovka: Sortirovka | null
  /** Щелчок по заголовку — как `pereklyuchitSortirovku` у `useTablitsaVAdrese`. */
  onSort: (kolonka: string) => void
  /** Галочки колонки; нет группы — нет и значка фильтра. */
  gruppa: GruppaFiltra | undefined
  /** Новый выбор колонки целиком — как `zadatVybor` у `useTablitsaVAdrese`. */
  onVybor: (kolonka: string, kody: string[]) => void
}

/**
 * Содержимое `th` широкой таблицы (через `zagolovok` у `DataTable`), как в
 * Google-таблицах: щелчок по названию сортирует, значок-воронка открывает
 * галочки колонки. Порядок сообщает `aria-sort` заголовка, значок
 * сортировки — только глазу.
 */
export function ZagolovokKolonki<T>({ kolonka, sortirovka, onSort, gruppa, onVybor }: Props<T>) {
  const znachok = useRef<HTMLButtonElement>(null)
  const [otkryta, otkryt] = useState(false)
  // Стабильная: от неё зависит слушатель нажатия снаружи, а панель
  // перерисовывается на каждой галочке.
  const zakryt = useCallback(() => otkryt(false), [])
  const napravlenie = sortirovka?.kolonka === kolonka.key ? sortirovka.napravlenie : null

  return (
    <span className="zagolovok">
      {kolonka.sort ? (
        // Текст — первым: по нему кнопка встаёт на базовую линию строки.
        // Значок первым поставил бы её по своему низу — строка шапки
        // выросла бы на 1,5–2 px.
        <button type="button" onClick={() => onSort(kolonka.key)}>
          {kolonka.title}
          <SortirovkaIcon napravlenie={napravlenie} />
        </button>
      ) : (
        kolonka.title
      )}
      {gruppa && (
        <button
          ref={znachok}
          type="button"
          className={gruppa.aktivna ? 'zagolovok-filtr zagolovok-filtr--aktiven' : 'zagolovok-filtr'}
          aria-label={imyaZnachka(kolonka.title, gruppa)}
          aria-haspopup="dialog"
          aria-expanded={otkryta}
          onClick={() => otkryt(!otkryta)}
        >
          <VoronkaIcon />
        </button>
      )}
      {/* Соседка значка, а не его дитя: события портала всплывают по
          дереву React, и щелчок внутри панели дошёл бы до значка. */}
      {gruppa && otkryta && (
        <Vsplyvashka
          yakor={znachok}
          vyravnivanie={kolonka.align ?? 'left'}
          nazvanie={`Фильтр: ${kolonka.title}`}
          onZakryt={zakryt}
        >
          <Galochki gruppa={gruppa} onVybor={onVybor} />
        </Vsplyvashka>
      )}
    </span>
  )
}

// Сколько отмечено — и значения без строк тоже: их отметка действует.
function imyaZnachka(nazvanie: string, gruppa: GruppaFiltra): string {
  const vybrano = gruppa.varianty.filter((v) => v.vybran).length
  return vybrano > 0 ? `Фильтр: ${nazvanie}, выбрано ${vybrano}` : `Фильтр: ${nazvanie}`
}
