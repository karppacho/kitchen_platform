import type { RefObject } from 'react'

import { ROLI, useSession } from '../auth/session'
import { ModalnayaPanel } from '../ui/ModalnayaPanel'
import { Nav } from './Nav'

export const ID_SHTORKI = 'shtorka-razdelov'

type Props = {
  otkryvatel: RefObject<HTMLButtonElement>
  onZakryt: () => void
}

/**
 * Шторка разделов на телефоне: тот же `Nav`, что в боковой панели
 * компьютера, — своего списка разделов у шторки нет. Внизу — кто вошёл и
 * «Выйти»: в шапке телефона им места нет, а шторка открывается одним
 * нажатием.
 */
export function Shtorka({ otkryvatel, onZakryt }: Props) {
  const { me, logout } = useSession()
  const roli = me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')

  return (
    <ModalnayaPanel
      id={ID_SHTORKI}
      storona="sleva"
      nazvanie="Разделы"
      zagolovok="Кухня"
      onZakryt={onZakryt}
      otkryvatel={otkryvatel}
      niz={
        <div className="shtorka-niz">
          <span className="shtorka-kto">
            <b>{me?.display_name}</b>
            <span className="shtorka-rol">{roli}</span>
          </span>
          <button
            type="button"
            className="shtorka-vyhod"
            onClick={() => {
              onZakryt()
              void logout()
            }}
          >
            Выйти
          </button>
        </div>
      }
    >
      <Nav onGo={onZakryt} />
    </ModalnayaPanel>
  )
}
