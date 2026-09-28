import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'

import { raspolozhit, SHIRINA_PANELI } from './raspolozhenie'
import './vsplyvashka.css'

type Props = {
  /** Значок, открывший панель: под ним она стоит, щелчок по нему — не
   *  «снаружи», на него возвращается фокус. */
  yakor: RefObject<HTMLElement>
  /** Выравнивание колонки — к какому краю значка прижать панель. */
  vyravnivanie: 'left' | 'right'
  /** Имя диалога — «Фильтр: Категория». */
  nazvanie: string
  /** Лучше стабильная (`useCallback`): от неё зависит слушатель нажатия
   *  снаружи — новая функция на каждой отрисовке переставляла бы его. */
  onZakryt: () => void
  children: ReactNode
}

// Куда встаёт фокус по Tab. Группа галочек (tabIndex -1) и сама панель
// сюда не входят: фокус на них бывает, но Tab на них не ведёт.
const TABUEMYE = [
  'button:not(:disabled)',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

type Mesto = { left: number; top: number }

// Сколько ждать click после нажатия. Он приходит, когда кнопку мыши или
// палец отпустили, — обычно через десятые доли секунды; секунды хватает и
// на медленное нажатие.
const ZHDAT_SHCHELCHKA_MS = 1000

/**
 * Погасить click, который придёт следом за нажатием, уже закрывшим панель.
 *
 * Слушатель — на document, а не в эффекте панели: панель убирают из
 * документа сразу после нажатия, до click, и очистка эффекта сняла бы его
 * раньше времени. Гасит один click и снимается. Касание, ушедшее в
 * прокрутку, click не присылает — тогда слушатель снимают следующее нажатие
 * или таймер: иначе он проглотил бы чужой щелчок, пришедший позже (Enter на
 * кнопке тоже шлёт click, без нажатия).
 */
function pogasitShchelchok() {
  const taymer = window.setTimeout(snyat, ZHDAT_SHCHELCHKA_MS)
  function pogasit(event: MouseEvent) {
    event.stopPropagation()
    event.preventDefault()
    snyat()
  }
  function snyat() {
    window.clearTimeout(taymer)
    document.removeEventListener('click', pogasit, true)
    document.removeEventListener('pointerdown', snyat, true)
  }
  // На погружении — раньше обработчиков React в корне приложения. Слушатель
  // нажатия ставится во время этого же нажатия, но на нём не сработает:
  // слушатели события берутся до обхода.
  document.addEventListener('click', pogasit, true)
  document.addEventListener('pointerdown', snyat, true)
}

/**
 * Всплывающая панель под значком — портал в `document.body`: внутри `th`
 * её обрезала бы прокрутка обёртки таблицы, а липкие `th` соседних колонок
 * легли бы поверх. Popover API и `<dialog>.showModal()` не годятся: в jsdom
 * 25 их нет, а тесты — на нём.
 *
 * Фокус заперт в панели (Tab по кругу), поэтому для программы чтения с
 * экрана это модальный диалог. Закрывают Escape, «Готово» и нажатие
 * снаружи; значок, открывший панель, «снаружи» не считается — повторный
 * щелчок по нему закрывает её обработчиком самого значка.
 *
 * События из портала всплывают по дереву React, а не DOM: панель — соседка
 * значка, а не его дитя, иначе щелчок внутри панели дошёл бы до значка.
 */
export function Vsplyvashka({ yakor, vyravnivanie, nazvanie, onZakryt, children }: Props) {
  const panel = useRef<HTMLDivElement>(null)
  const [mesto, zadatMesto] = useState<Mesto | null>(null)

  // Панель едет за значком. Место не изменилось (прокрутка страницы: в
  // документе всё на месте) — состояние прежнее, перерисовки нет.
  const postavit = useCallback(() => {
    const znachok = yakor.current
    if (znachok === null) return
    const novoe = raspolozhit(znachok.getBoundingClientRect(), vyravnivanie, document.documentElement.clientWidth, {
      x: window.scrollX,
      y: window.scrollY,
    })
    zadatMesto((bylo) => (bylo?.left === novoe.left && bylo.top === novoe.top ? bylo : novoe))
  }, [yakor, vyravnivanie])

  // После каждой отрисовки, первая — тоже: панель не мелькнёт в углу.
  // Значок сдвигает и главное действие самой панели: у таблицы нет
  // фиксированной раскладки — галочка меняет строки, ширины колонок
  // пересчитываются; при 0 строках колонки сжимаются до заголовков; над
  // таблицей появляются фишки, и шапка уезжает вниз. Панель при этом
  // перерисовывается (новая группа), а раскладка к эффекту уже новая.
  // Без зависимостей намеренно; зацикливания нет — то же место не меняет
  // состояния.
  useLayoutEffect(() => {
    postavit()
  })

  // Сдвиги без перерисовки панели: таблицу прокрутили вбок внутри обёртки
  // (эта прокрутка не всплывает — слушаем на погружении) или сменили
  // ширину окна.
  useLayoutEffect(() => {
    window.addEventListener('resize', postavit)
    document.addEventListener('scroll', postavit, true)
    return () => {
      window.removeEventListener('resize', postavit)
      document.removeEventListener('scroll', postavit, true)
    }
  }, [postavit])

  // Фокус — на первое, что берёт Tab (первую галочку), когда панель уже на
  // месте: focus() прокручивает страницу к элементу, а до расстановки
  // панель стоит в конце body.
  const postavlena = mesto !== null
  useLayoutEffect(() => {
    if (postavlena) panel.current?.querySelector<HTMLElement>(TABUEMYE)?.focus()
  }, [postavlena])

  // Закрылась с фокусом внутри (Escape, «Готово», значок, нажатие снаружи)
  // — фокус на значок, а не на body: клавиатура продолжает оттуда, откуда
  // открыла. Нажатие снаружи на другую кнопку браузер потом всё равно
  // переставит на неё. Очистка идёт до того, как панель уберут из
  // документа, — фокус ещё внутри.
  useLayoutEffect(() => {
    const svoya = panel.current
    const znachok = yakor.current
    return () => {
      if (svoya?.contains(document.activeElement)) znachok?.focus()
    }
  }, [yakor])

  // «Снаружи» — по нажатию (pointerdown), а не по click. Галочка значения
  // без строк при снятии исчезает: React убирает её, пока click ещё
  // всплывает, и до document он дошёл бы с целью вне документа — `contains`
  // ответил бы «не внутри», и панель закрылась бы сама. Нажатие приходит
  // раньше, пока галочка на месте. На погружении — чтобы ничей
  // stopPropagation не спрятал нажатие.
  //
  // Панель модальная, и нажатие снаружи только закрывает её. Под шапкой
  // почти всё место — строки таблицы, так что «щёлкнуть мимо» — чаще всего
  // щелчок по строке, и он открыл бы карточку блюда. Его click гасим. Строка
  // — любая `tbody tr`, а не только открывающая: дело в месте, куда
  // щёлкают, чтобы закрыть, а погашенный щелчок по строке без действия
  // ничего не отнимает; и таблице не нужно метить строки для панели. Кнопки
  // шапки — воронка другой колонки, сортировка — и всё прочее срабатывают с
  // первого нажатия: по ним щёлкают ради них самих.
  useEffect(() => {
    function nazhatie(event: PointerEvent) {
      const tsel = event.target
      if (!(tsel instanceof Node)) return
      if (panel.current?.contains(tsel) || yakor.current?.contains(tsel)) return
      if (tsel instanceof Element && tsel.closest('tbody tr') !== null) pogasitShchelchok()
      onZakryt()
    }
    document.addEventListener('pointerdown', nazhatie, true)
    return () => document.removeEventListener('pointerdown', nazhatie, true)
  }, [yakor, onZakryt])

  function klavisha(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onZakryt()
      return
    }
    if (event.key !== 'Tab' || panel.current === null) return
    const spisok = [...panel.current.querySelectorAll<HTMLElement>(TABUEMYE)]
    const pervyi = spisok[0]
    const posledniy = spisok[spisok.length - 1]
    if (pervyi === undefined || posledniy === undefined) return
    const seychas = document.activeElement
    // Фокус на самой группе галочек (галочек не осталось) или на панели
    // (щёлкнули по пустому месту) — их нет в списке. Браузер повёл бы от
    // них по порядку документа, и Shift+Tab ушёл бы из портала на шапку
    // таблицы. С них — как с края круга.
    const vKruge = spisok.some((el) => el === seychas)
    if (event.shiftKey && (!vKruge || seychas === pervyi)) {
      event.preventDefault()
      posledniy.focus()
    } else if (!event.shiftKey && (!vKruge || seychas === posledniy)) {
      event.preventDefault()
      pervyi.focus()
    }
  }

  return createPortal(
    // tabIndex -1: щелчок по пустому месту панели ставит фокус на неё, а
    // не на body — Escape и Tab продолжают работать.
    <div
      ref={panel}
      role="dialog"
      aria-modal="true"
      aria-label={nazvanie}
      tabIndex={-1}
      className="vsplyvashka"
      // Ширина — отсюда, а не из CSS: по ней же считается прижим к краю окна.
      style={{ width: SHIRINA_PANELI, ...mesto }}
      onKeyDown={klavisha}
    >
      <div className="vsplyvashka-soderzhimoe">{children}</div>
      <div className="vsplyvashka-niz">
        <button type="button" className="vsplyvashka-gotovo" onClick={onZakryt}>
          Готово
        </button>
      </div>
    </div>,
    document.body,
  )
}
