import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'
import { MenuIcon } from '../ui/Icons'
import { Nav } from './Nav'
import { Svezhest } from './Svezhest'
import './shell.css'

export function Layout() {
  const { me, logout } = useSession()
  const [menyuOtkryto, otkryt] = useState(false)

  return (
    <div className="obolochka">
      <header className="shapka">
        <button
          type="button"
          className="shapka-menyu"
          aria-label="Разделы"
          aria-expanded={menyuOtkryto}
          onClick={() => otkryt(!menyuOtkryto)}
        >
          <MenuIcon />
        </button>
        <span className="shapka-nazvanie">Кухня</span>
        <span className="shapka-kto">
          <b>{me?.display_name}</b>
          <span className="shapka-rol">
            {me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')}
          </span>
        </span>
        <button type="button" className="shapka-vyhod" onClick={() => void logout()}>
          Выйти
        </button>
      </header>

      <div className="obolochka-telo">
        <aside className={menyuOtkryto ? 'bok bok--otkryt' : 'bok'}>
          <Nav onGo={() => otkryt(false)} />
        </aside>
        <main className="soderzhimoe">
          <Svezhest />
          <Outlet />
        </main>
      </div>
    </div>
  )
}
