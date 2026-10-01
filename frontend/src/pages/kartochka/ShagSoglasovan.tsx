import type { Draft, Soglasovanie } from '../../api/types'
import { RamkaShaga, useShag, ZAGOLOVOK_SHAGA } from './RamkaShaga'

/**
 * Шаг 6. Согласован ли продукт — «Да» или «Отбракован».
 *
 * Ответ и есть переход, одним касанием и одной правкой: «Да» — к фото
 * продукта, «Отбракован» — сразу к итогу, как у бота: фото продукта и
 * описание отбракованному не нужны. В лист ляжет ровно «Да» или
 * «Отбракован».
 */
export function ShagSoglasovan({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik)
  const otvetit = (otvet: Soglasovanie) =>
    upravlenie.dalee({ approval: otvet }, otvet === 'Отбракован' ? 'summary' : undefined)

  return (
    <RamkaShaga upravlenie={upravlenie}>
      <p className="kartochka-poyasnenie">
        «Отбракован» — сразу к отправке: фото продукта и описание для него не нужны.
      </p>
      <div className="kartochka-otvety" role="group" aria-labelledby={ZAGOLOVOK_SHAGA}>
        {(['Да', 'Отбракован'] as const).map((otvet) => (
          <button
            key={otvet}
            type="button"
            aria-pressed={chernovik.approval === otvet}
            disabled={upravlenie.zanyato}
            onClick={() => otvetit(otvet)}
          >
            {otvet}
          </button>
        ))}
      </div>
    </RamkaShaga>
  )
}
