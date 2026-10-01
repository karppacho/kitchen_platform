import { useState } from 'react'

import { useVarianty } from '../../api/kartochki'
import type { Draft } from '../../api/types'
import { estDannye, Sostoyanie } from '../../ui/Sostoyanie'
import { RamkaShaga, useShag, ZAGOLOVOK_SHAGA } from './RamkaShaga'

/**
 * Шаг 2. Категория — из списка или своя.
 *
 * Список — 15 категорий бота и категории карточек в листе, частые первыми.
 * Общего редактируемого списка нет, поэтому «Другая…» — не категория, а
 * действие экрана: своя категория появится в списке, когда карточка с ней
 * ляжет в лист.
 */
export function ShagKategoriya({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik)
  const varianty = useVarianty()
  const spisok = varianty.data?.categories ?? []
  const [kategoriya, zadatKategoriyu] = useState(chernovik.category)
  const [drugayaVybrana, vybratDruguyu] = useState(false)
  const tekst = kategoriya.trim()

  // Категория черновика не из списка — в прошлый раз повар вписал её сам.
  // Пока список не пришёл, судить об этом не по чему.
  const svoya =
    drugayaVybrana || (!varianty.isPending && kategoriya !== '' && !spisok.includes(kategoriya))

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={tekst !== ''}
      onDalee={() => upravlenie.dalee({ category: tekst })}
    >
      {/* Список не пришёл — свою категорию вписать всё равно можно. */}
      {!estDannye(varianty) && <Sostoyanie query={varianty} />}
      <div className="kartochka-varianty" role="radiogroup" aria-labelledby={ZAGOLOVOK_SHAGA}>
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
      {svoya && (
        <label className="kartochka-pole">
          <span>Своя категория</span>
          <input
            className="kartochka-vvod"
            type="text"
            value={kategoriya}
            maxLength={100}
            autoComplete="off"
            onChange={(sobytie) => zadatKategoriyu(sobytie.target.value)}
          />
        </label>
      )}
    </RamkaShaga>
  )
}
