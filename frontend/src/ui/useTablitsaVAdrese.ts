import { useLayoutEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'

import { poiskIliPusto, prochitatAdres, zapisatAdres } from '../domain/adres'
import type { OpisanieKolonki, Sortirovka, SostoyanieTablitsy } from '../domain/tablitsa'
import { useOtlozhennyiPoisk } from './useOtlozhennyiPoisk'

export type TablitsaVAdrese = {
  /** Из адреса, но поиск — живой ввод: строки отбираются сразу, не
      дожидаясь, пока он уйдёт в адрес. */
  sostoyanie: SostoyanieTablitsy
  /** Текст в поле поиска как есть, с пробелами. */
  vvod: string
  zadatVvod: (vvod: string) => void
  /** Щелчок по заголовку: по возрастанию → по убыванию → выключено;
      другая колонка начинает с возрастания. */
  pereklyuchitSortirovku: (kolonka: string) => void
  /** Выбор «Сортировка» на узком экране. */
  zadatSortirovku: (s: Sortirovka | null) => void
  zadatVybor: (kolonka: string, kody: readonly string[]) => void
  /** Поиск и фильтры, но не сортировку: это вид, а не отбор. */
  sbrositVsyo: () => void
  /** Адрес списка с поиском, ещё не ушедшим в адрес: `?…` или `''`, как
      `location.search`. Для обработчиков событий — возврат из карточки. */
  adresSeychas: () => string
}

/**
 * Сортировка, фильтры и поиск таблицы, живущие в адресе (правила — в
 * `domain/adres.ts`). Запись заменяет текущую запись истории, а не
 * добавляет новую: «назад» ведёт на прошлый экран, а не по каждому щелчку.
 *
 * `kolonki` — константа экрана: новый массив на каждый рендер пересчитывал
 * бы состояние, а за ним и таблицу, на каждый рендер.
 */
export function useTablitsaVAdrese<T>(kolonki: readonly OpisanieKolonki<T>[]): TablitsaVAdrese {
  const [params, setParams] = useSearchParams()

  // Все записи идут через izmenit, и каждая строится от свежих параметров,
  // а не от параметров рендера: два изменения в одном обработчике (сортировка
  // и галочка) иначе стёрли бы друг друга. Функциональная форма
  // setSearchParams не спасает — в React Router 6 она видит параметры того же
  // рендера. Поэтому ref: запись обновляет его сразу, а адрес, сменившийся
  // снаружи (ссылка, «назад»), — до того, как успеет сработать обработчик.
  // Держится на том, что навигация — срочное обновление: флага
  // v7_startTransition в приложении нет; включат его — пересмотреть.
  const svezhie = useRef(params)
  useLayoutEffect(() => {
    // useSearchParams меняет объект только при смене адреса, так что рендер
    // со старыми параметрами не затрёт записанное, но ещё не отрисованное.
    svezhie.current = params
  }, [params])

  function izmenit(fn: (s: SostoyanieTablitsy) => SostoyanieTablitsy) {
    const prezhnie = svezhie.current
    const novye = zapisatAdres(prezhnie, fn(prochitatAdres(prezhnie, kolonki)), kolonki)
    // Тот же адрес — не навигация: даже replace заменяет запись истории на
    // новую — с другим key и без state, с которым пришли на экран.
    if (novye.toString() === prezhnie.toString()) return
    svezhie.current = novye
    setParams(novye, { replace: true })
  }

  const izAdresa = useMemo(() => prochitatAdres(params, kolonki), [params, kolonki])
  const [vvod, zadatVvod] = useOtlozhennyiPoisk(izAdresa.poisk, (poisk) => izmenit((s) => ({ ...s, poisk })))
  const sostoyanie = useMemo(() => ({ ...izAdresa, poisk: poiskIliPusto(vvod) }), [izAdresa, vvod])

  return {
    sostoyanie,
    vvod,
    zadatVvod,
    pereklyuchitSortirovku: (kolonka) => izmenit((s) => ({ ...s, sortirovka: sleduyushchaya(s.sortirovka, kolonka) })),
    zadatSortirovku: (sortirovka) => izmenit((s) => ({ ...s, sortirovka })),
    zadatVybor: (kolonka, kody) => izmenit((s) => ({ ...s, vybor: { ...s.vybor, [kolonka]: kody } })),
    sbrositVsyo: () => {
      izmenit((s) => ({ ...s, poisk: '', vybor: {} }))
      // Ввод — тоже: иначе отложенная запись набранного, но ещё не ушедшего
      // в адрес поиска вернула бы его через 300 мс после сброса.
      zadatVvod('')
    },
    adresSeychas: () => {
      const p = svezhie.current
      const adres = zapisatAdres(p, { ...prochitatAdres(p, kolonki), poisk: vvod }, kolonki).toString()
      return adres === '' ? '' : `?${adres}`
    },
  }
}

function sleduyushchaya(tekushchaya: Sortirovka | null, kolonka: string): Sortirovka | null {
  if (tekushchaya?.kolonka !== kolonka) return { kolonka, napravlenie: 'vozr' }
  return tekushchaya.napravlenie === 'vozr' ? { kolonka, napravlenie: 'ubyv' } : null
}
