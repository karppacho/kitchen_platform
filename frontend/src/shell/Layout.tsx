import { useCallback, useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'
import { MenuIcon } from '../ui/Icons'
import { useWide } from '../ui/useWide'
import { Granitsa } from './Granitsa'
import { Nav } from './Nav'
import { razdelPoAdresu } from './razdelPoAdresu'
import { RAZDELY } from './razdely'
import { ID_SHTORKI, Shtorka } from './Shtorka'
import './shell.css'
import { Svezhest } from './Svezhest'
import { Vkladki } from './Vkladki'

/**
 * Оболочка. Компьютер (от 1080 px): шапка с именем и «Выйти», боковая
 * панель с меню. Телефон: липкая шапка с кнопкой меню и названием текущего
 * раздела, шторка слева поверх страницы, вкладки внизу. Порог — `useWide`;
 * CSS ветвится по классу `obolochka--telefon`, а не по своему медиазапросу.
 */
export function Layout() {
  const { me, logout } = useSession()
  const wide = useWide()
  const { pathname } = useLocation()
  const [menyuOtkryto, otkryt] = useState(false)
  const knopkaMenyu = useRef<HTMLButtonElement>(null)
  const zakryt = useCallback(() => otkryt(false), [])

  // Окно стало широким при открытой шторке — шторки на компьютере нет.
  useEffect(() => {
    if (wide) otkryt(false)
  }, [wide])

  // Адрес сменился мимо пункта шторки: системная «Назад» на Android при
  // открытой шторке меняет страницу под затемнением, и шторка оставалась бы
  // открытой с `inert` на всей странице. `onGo` у Nav при этом нужен: он
  // закрывает и при нажатии на текущий раздел, когда адрес не меняется.
  useEffect(() => {
    otkryt(false)
  }, [pathname])

  const razdel = razdelPoAdresu(pathname, RAZDELY)
  const roli = me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')

  return (
    <div className={wide ? 'obolochka' : 'obolochka obolochka--telefon'}>
      <header className="shapka">
        {!wide && (
          <button
            ref={knopkaMenyu}
            type="button"
            className="shapka-menyu"
            aria-label="Разделы"
            aria-expanded={menyuOtkryto}
            aria-controls={menyuOtkryto ? ID_SHTORKI : undefined}
            onClick={() => otkryt(!menyuOtkryto)}
          >
            <MenuIcon />
          </button>
        )}
        <span className="shapka-nazvanie">{wide ? 'Кухня' : (razdel?.nazvanie ?? 'Кухня')}</span>
        {wide && (
          <>
            <span className="shapka-kto">
              <b>{me?.display_name}</b>
              <span className="shapka-rol">{roli}</span>
            </span>
            <button type="button" className="shapka-vyhod" onClick={() => void logout()}>
              Выйти
            </button>
          </>
        )}
      </header>

      <div className="obolochka-telo">
        {wide && (
          <aside className="bok">
            <Nav />
          </aside>
        )}
        <main className="soderzhimoe">
          <Svezhest />
          {/* Ключ по адресу: упавший экран не тянет ошибку на соседний раздел. */}
          <Granitsa key={pathname}>
            <Outlet />
          </Granitsa>
        </main>
      </div>

      {!wide && menyuOtkryto && <Shtorka otkryvatel={knopkaMenyu} onZakryt={zakryt} />}
      {!wide && <Vkladki />}
    </div>
  )
}
