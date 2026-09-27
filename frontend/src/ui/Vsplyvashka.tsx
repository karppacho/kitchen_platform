import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
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

  // Место — до первой отрисовки, так что панель не мелькнёт в углу. Значок
  // сдвигается, когда таблицу прокручивают вбок внутри обёртки (эта
  // прокрутка не всплывает — слушаем на погружении) и когда меняется
  // ширина окна; панель едет за ним. Прокрутка страницы места в документе
  // не меняет — тогда состояние прежнее и перерисовки нет.
  useLayoutEffect(() => {
    function postavit() {
      const znachok = yakor.current
      if (znachok === null) return
      const novoe = raspolozhit(znachok.getBoundingClientRect(), vyravnivanie, document.documentElement.clientWidth, {
        x: window.scrollX,
        y: window.scrollY,
      })
      zadatMesto((bylo) => (bylo?.left === novoe.left && bylo.top === novoe.top ? bylo : novoe))
    }
    postavit()
    window.addEventListener('resize', postavit)
    document.addEventListener('scroll', postavit, true)
    return () => {
      window.removeEventListener('resize', postavit)
      document.removeEventListener('scroll', postavit, true)
    }
  }, [yakor, vyravnivanie])

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
  useEffect(() => {
    function nazhatie(event: PointerEvent) {
      const tsel = event.target
      if (!(tsel instanceof Node)) return
      if (panel.current?.contains(tsel) || yakor.current?.contains(tsel)) return
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
