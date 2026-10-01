import { useState } from 'react'

import { useVarianty } from '../../api/kartochki'
import type { Draft } from '../../api/types'
import { kakVSpiske } from '../../domain/poisk'
import { estDannye, Sostoyanie } from '../../ui/Sostoyanie'
import {
  OshibkaUPolya,
  RamkaShaga,
  svoistvaPolya,
  useShag,
  ZAGOLOVOK_SHAGA,
} from './RamkaShaga'

const POLYA = ['category'] as const

/**
 * Шаг 2. Категория — из списка или своя.
 *
 * Список — 15 категорий бота и категории карточек в листе, частые первыми.
 * Общего редактируемого списка нет, поэтому «Другая…» — не категория, а
 * действие экрана: своя категория появится в списке, когда карточка с ней
 * ляжет в лист.
 */
export function ShagKategoriya({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik, POLYA)
  const varianty = useVarianty()
  const spisok = varianty.data?.categories ?? []
  const [kategoriya, zadatKategoriyu] = useState(chernovik.category)
  const [drugayaVybrana, vybratDruguyu] = useState(false)
  // «сыры» во «Другая…» — это «Сыры» из списка, а не новая категория.
  const vybrano = kakVSpiske(kategoriya, spisok)

  // Категория черновика не из списка — в прошлый раз повар вписал её сам.
  // Пока список не пришёл, судить об этом не по чему.
  const svoya =
    drugayaVybrana || (!varianty.isPending && kategoriya !== '' && !spisok.includes(kategoriya))

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={vybrano !== ''}
      onDalee={() => upravlenie.dalee({ category: vybrano })}
      polya={{ category: vybrano }}
    >
      {/* Список не пришёл — свою категорию вписать всё равно можно. */}
      {!estDannye(varianty) && <Sostoyanie query={varianty} />}
      {/* Отказ сервера по категории ведёт фокус к ней: к своей категории,
          если повар вписал её, иначе — к списку (он фокусируется только
          так, с клавиатуры в него попадают по радиокнопкам). */}
      <div
        className="kartochka-varianty"
        role="radiogroup"
        aria-labelledby={ZAGOLOVOK_SHAGA}
        {...(svoya ? {} : { ...svoistvaPolya(upravlenie, 'category'), tabIndex: -1 })}
      >
        {spisok.map((k) => (
          <label key={k}>
            <input
              type="radio"
              name="kategoriya"
              checked={!svoya && kategoriya === k}
              onChange={() => {
                vybratDruguyu(false)
                zadatKategoriyu(k)
              }}
            />
            {k}
          </label>
        ))}
        <label>
          <input
            type="radio"
            name="kategoriya"
            checked={svoya}
            onChange={() => {
              vybratDruguyu(true)
              zadatKategoriyu('')
            }}
          />
          Другая…
        </label>
      </div>
      <OshibkaUPolya upravlenie={upravlenie} pole="category" />
      {svoya && (
        <label className="kartochka-pole">
          <span>Своя категория</span>
          <input
            className="kartochka-vvod"
            type="text"
            value={kategoriya}
            maxLength={100}
            autoComplete="off"
            {...svoistvaPolya(upravlenie, 'category')}
            onChange={(sobytie) => zadatKategoriyu(sobytie.target.value)}
          />
        </label>
      )}
    </RamkaShaga>
  )
}
