import { useState } from 'react'

import type { Draft } from '../../api/types'
import {
  OshibkaUPolya,
  RamkaShaga,
  svoistvaPolya,
  useShag,
  ZAGOLOVOK_SHAGA,
} from './RamkaShaga'

const POLYA = ['description'] as const

/** Предел сервера: длиннее он откажет, а молча обрезать набранное нельзя. */
const PREDEL = 2000

/**
 * Шаг 8. Описание — необязательно, свободным текстом.
 *
 * Единственное поле, где можно переносить строки: в лист оно уходит как
 * набрано. Пустое — тоже можно, «Далее» доступна всегда.
 */
export function ShagOpisanie({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik, POLYA)
  const [opisanie, zadatOpisanie] = useState(chernovik.description)

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee
      onDalee={() => upravlenie.dalee({ description: opisanie })}
      polya={{ description: opisanie }}
    >
      <p className="kartochka-poyasnenie" id="kartochka-opisanie-poyasnenie">
        Необязательно: что важно знать о продукте — вкус, как ведёт себя в работе, чем
        отличается от прежнего.
      </p>
      <textarea
        className="kartochka-vvod"
        rows={5}
        value={opisanie}
        maxLength={PREDEL}
        aria-labelledby={ZAGOLOVOK_SHAGA}
        {...svoistvaPolya(upravlenie, 'description', 'kartochka-opisanie-poyasnenie')}
        onChange={(sobytie) => zadatOpisanie(sobytie.target.value)}
      />
      <OshibkaUPolya upravlenie={upravlenie} pole="description" />
    </RamkaShaga>
  )
}
