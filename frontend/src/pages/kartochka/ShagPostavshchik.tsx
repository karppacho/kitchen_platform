import { useState } from 'react'

import { useVarianty } from '../../api/kartochki'
import type { Draft } from '../../api/types'
import { kakVSpiske } from '../../domain/poisk'
import {
  OshibkaUPolya,
  RamkaShaga,
  svoistvaPolya,
  useShag,
  ZAGOLOVOK_SHAGA,
} from './RamkaShaga'

/** Сколько поставщиков подсказывать. Больше — список длиннее экрана
 *  телефона; нужный находится парой букв. */
const PODSKAZOK = 8

const PODSKAZKI_ID = 'kartochka-postavshchiki'

const POLYA = ['supplier'] as const

/**
 * Шаг 1. Поставщик — из прежних карточек касанием или новый текстом.
 *
 * Поставщик и название нужны раньше фото: по ним называется файл фото в
 * хранилище.
 */
export function ShagPostavshchik({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik, POLYA)
  const varianty = useVarianty()
  const [postavshchik, zadatPostavshchika] = useState(chernovik.supplier)
  const tekst = postavshchik.trim()
  const spisok = varianty.data?.suppliers ?? []

  // Частые первыми — так их отдаёт сервер; набранное сужает список.
  const iskomoe = tekst.toLocaleLowerCase('ru')
  const podskazki = spisok
    .filter((p) => p !== tekst && p.toLocaleLowerCase('ru').includes(iskomoe))
    .slice(0, PODSKAZOK)

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={tekst !== ''}
      // «метро» уходит как «Метро» из списка — иначе в листе станет два поставщика.
      onDalee={() => upravlenie.dalee({ supplier: kakVSpiske(tekst, spisok) })}
    >
      <input
        className="kartochka-vvod"
        type="text"
        aria-labelledby={ZAGOLOVOK_SHAGA}
        {...svoistvaPolya(upravlenie, 'supplier')}
        value={postavshchik}
        // Предел сервера: длиннее он отказал бы, а обрезать текст повара молча нельзя.
        maxLength={200}
        autoComplete="off"
        onChange={(sobytie) => zadatPostavshchika(sobytie.target.value)}
      />
      <OshibkaUPolya upravlenie={upravlenie} pole="supplier" />
      {podskazki.length > 0 && (
        <>
          <p id={PODSKAZKI_ID} className="kartochka-poyasnenie">
            Из прежних карточек:
          </p>
          <ul className="kartochka-podskazki" aria-labelledby={PODSKAZKI_ID}>
            {podskazki.map((p) => (
              <li key={p}>
                <button type="button" onClick={() => zadatPostavshchika(p)}>
                  {p}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </RamkaShaga>
  )
}
