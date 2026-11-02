import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import { KrestikIcon } from './Icons'
import './modalnayaPanel.css'

type Props = {
  /** id диалога — на него указывает `aria-controls` открывшей кнопки. */
  id: string
  /** Откуда выезжает: шторка разделов — слева, панель отбора — снизу. */
  storona: 'sleva' | 'snizu'
  /** Имя диалога для программы чтения с экрана. */
  nazvanie: string
  /** Видимый заголовок в шапке панели; по умолчанию — `nazvanie`. */
  zagolovok?: string
  onZakryt: () => void
  /** Кнопка, открывшая панель: на неё возвращается фокус при закрытии. */
  otkryvatel: RefObject<HTMLElement>
  children: ReactNode
  /** Прилипший низ панели — кнопки «Показать N», «Сбросить всё». */
  niz?: ReactNode
}

// Куда встаёт фокус по Tab (как у Vsplyvashka).
const TABUEMYE = [
  'button:not(:disabled)',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

/**
 * Модальная панель поверх страницы — портал в `document.body`: шторка
 * разделов и панель сортировки и фильтров на телефоне. Не `<dialog>`: в
 * jsdom 25 нет `showModal`, а тесты — на нём (та же причина, что у
 * `Vsplyvashka`).
 *
 * Пока панель открыта: всё остальное в `body` — `inert` (клавиатура и
 * программа чтения с экрана не уходят под затемнение), страница не
 * прокручивается (на iOS `overflow: hidden` у body не действует — поэтому
 * `position: fixed` с возвратом прокрутки при закрытии), фокус — внутри,
 * Tab по кругу, Escape закрывает, при закрытии фокус — на открывшую кнопку.
 *
 * Затемнение закрывает панель по click, а не по нажатию (pointerdown), как
 * `Vsplyvashka`: на телефоне браузер синтезирует click после touchend по
 * элементу под пальцем, и если панель убрать уже на нажатии, click придёт
 * в строку списка или ссылку под затемнением — «призрачный» щелчок. По
 * click цель — само затемнение, гасить ничего не нужно (у `Vsplyvashka`
 * слушатель стоит на document, оттого ей и приходится гасить).
 */
export function ModalnayaPanel({ id, storona, nazvanie, zagolovok, onZakryt, otkryvatel, children, niz }: Props) {
  const koren = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  // inert на соседях по body и запрет прокрутки — на время жизни панели.
  useLayoutEffect(() => {
    const svoy = koren.current
    const sosedi = [...document.body.children].filter((el) => el !== svoy && !el.hasAttribute('inert'))
    for (const el of sosedi) el.setAttribute('inert', '')

    const body = document.body
    const prokrutka = window.scrollY
    const bylo = { position: body.style.position, top: body.style.top, width: body.style.width, overflow: body.style.overflow }
    body.style.position = 'fixed'
    body.style.top = `-${prokrutka}px`
    body.style.width = '100%'
    body.style.overflow = 'hidden'
    return () => {
      for (const el of sosedi) el.removeAttribute('inert')
      body.style.position = bylo.position
      body.style.top = bylo.top
      body.style.width = bylo.width
      body.style.overflow = bylo.overflow
      // В jsdom scrollTo не реализован; при нулевой прокрутке возвращать нечего.
      if (prokrutka) window.scrollTo(0, prokrutka)
    }
  }, [])

  // Фокус — на первый табуемый элемент (кнопку «Закрыть»), иначе на панель.
  useLayoutEffect(() => {
    const pervyi = panel.current?.querySelector<HTMLElement>(TABUEMYE)
    ;(pervyi ?? panel.current)?.focus()
  }, [])

  // Закрылась с фокусом внутри — фокус на открывшую кнопку, а не на body.
  // Очистка идёт до того, как панель уберут из документа, — фокус ещё внутри.
  useLayoutEffect(() => {
    const svoy = koren.current
    const knopka = otkryvatel.current
    return () => {
      if (svoy?.contains(document.activeElement)) knopka?.focus()
    }
  }, [otkryvatel])

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
    <div ref={koren} className={`modalnaya modalnaya--${storona}`}>
      {/* click, а не pointerdown: иначе синтезированный после touchend click
          попал бы в то, что под затемнением (см. описание компонента). */}
      <div className="modalnaya-fon" onClick={onZakryt} />
      <div
        ref={panel}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-label={nazvanie}
        tabIndex={-1}
        className="modalnaya-panel"
        onKeyDown={klavisha}
      >
        <div className="modalnaya-shapka">
          <span className="modalnaya-zagolovok">{zagolovok ?? nazvanie}</span>
          <button type="button" className="modalnaya-zakryt" aria-label="Закрыть" onClick={onZakryt}>
            <KrestikIcon />
          </button>
        </div>
        <div className="modalnaya-soderzhimoe">{children}</div>
        {niz !== undefined && <div className="modalnaya-niz">{niz}</div>}
      </div>
    </div>,
    document.body,
  )
}
