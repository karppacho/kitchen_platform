import { useMemo } from 'react'

import { useIngredients, useReconciliation } from '../api/queries'
import type { Ingredient, LinkStatus, ReconciliationRow } from '../api/types'
import { NameDiff } from '../ui/NameDiff'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'
import './sverka.css'

// Три группы требуют разных действий (раздел 8 спеки): выбрать из вариантов,
// подтвердить или отвергнуть, завести или отложить.
const GRUPPY: { status: LinkStatus; zagolovok: string; chto: string; deystviya: string[] }[] = [
  {
    status: 'ambiguous',
    zagolovok: 'Несколько совпадений',
    chto:
      'В справочнике несколько активных позиций с этим именем. Надо выбрать, ' +
      'какая имелась в виду — различает их не имя, а цена и единица.',
    deystviya: ['Связать с выбранной', 'Отложить'],
  },
  {
    status: 'candidate',
    zagolovok: 'Есть похожее',
    chto: 'Точного совпадения нет. Надо подтвердить предложенное или отвергнуть.',
    deystviya: ['Связать', 'Отвергнуть'],
  },
  {
    status: 'orphan',
    zagolovok: 'Пары нет',
    chto:
      'Ингредиент оформлен поваром, но калькулятор его не видит. Надо завести ' +
      'позицию в справочник или отложить.',
    deystviya: ['Завести в справочник', 'Отложить'],
  },
]

export function Reconciliation() {
  const query = useReconciliation()
  // Список справочника берётся один раз и держится под рукой: ручка сверки
  // отдаёт у кандидатов только id и имя, а выбирают по цене.
  const spravochnik = useIngredients()

  // null — справочник ещё не пришёл: прочерк вместо цены соврал бы, что
  // цены нет вовсе.
  const poId = useMemo(() => {
    if (!spravochnik.data) return null
    return new Map(spravochnik.data.map((stroka) => [stroka.id, stroka]))
  }, [spravochnik.data])

  if (!query.isSuccess) return <Sostoyanie query={query} />
  const svodka = query.data

  return (
    <section>
      <h1>Сверка справочника</h1>
      <p className="poyasnenie">
        Справочник ингредиентов и карточки, которые заполняют повара, лежат в разных
        таблицах и не связаны между собой. Что склеилось по точному совпадению имени —
        склеилось; остальное решает человек.
      </p>

      <div className="krupno">
        <Pokazatel podpis="Всего карточек" znachenie={svodka.total} />
        <Pokazatel podpis="Склеено" znachenie={svodka.linked} />
        <Pokazatel podpis="Требуют решения" znachenie={svodka.needs_human} vnimanie />
      </div>

      {spravochnik.isError && (
        <>
          {/* Молчать нельзя: без цен тёзки неотличимы, а экран выглядел бы
              целым. */}
          <p className="poyasnenie">
            Цены из справочника не загрузились — тёзок по ним пока не различить.
          </p>
          <Sostoyanie query={spravochnik} />
        </>
      )}

      {GRUPPY.map((gruppa) => {
        const stroki = svodka.rows.filter((r) => r.link_status === gruppa.status)
        if (stroki.length === 0) return null
        return (
          <div key={gruppa.status}>
            <h2>
              {gruppa.zagolovok} — {stroki.length}
            </h2>
            <p className="poyasnenie">{gruppa.chto}</p>
            {stroki.map((stroka) => (
              <Kartochka
                key={stroka.card_id}
                stroka={stroka}
                deystviya={gruppa.deystviya}
                poId={poId}
              />
            ))}
          </div>
        )
      })}
    </section>
  )
}

function Pokazatel({
  podpis,
  znachenie,
  vnimanie,
}: {
  podpis: string
  znachenie: number
  vnimanie?: boolean
}) {
  return (
    <div className="pokazatel">
      <span className="pokazatel-podpis">{podpis}</span>
      <span
        className={
          vnimanie ? 'pokazatel-znachenie pokazatel-znachenie--vnimanie' : 'pokazatel-znachenie'
        }
      >
        {znachenie}
      </span>
    </div>
  )
}

function Kartochka({
  stroka,
  deystviya,
  poId,
}: {
  stroka: ReconciliationRow
  deystviya: string[]
  poId: Map<number, Ingredient> | null
}) {
  return (
    <article className="sverka-kartochka" data-testid={`kartochka-${stroka.card_id}`}>
      <header>
        {/* Имена показываются как есть, с опечатками и лишними пробелами:
            подчистить за шефа значило бы скрыть, что запись требует
            внимания. */}
        <b>{stroka.name}</b>
        <span className="postavshchik">{stroka.supplier}</span>
      </header>

      {stroka.candidates.length > 0 ? (
        <ul className="varianty">
          {stroka.candidates.map((kandidat) => {
            const ingredient = poId?.get(kandidat.ingredient_id)
            return (
              <li key={kandidat.ingredient_id}>
                <label>
                  {/* Предвыбранного варианта нет. Подсвеченное как очевидное
                      совпадение человек примет не глядя. */}
                  <input type="radio" name={`vybor-${stroka.card_id}`} disabled />
                  <NameDiff a={kandidat.name} b={stroka.name} />
                  <span className="variant-otlichie">
                    <span className="variant-id">{kandidat.legacy_id}</span>
                    {poId &&
                      (ingredient ? (
                        <Num
                          value={ingredient.price_per_kg}
                          fraction={2}
                          unit={`₽/${ingredient.unit}`}
                        />
                      ) : (
                        // Справочник пришёл, а позиции в нём нет.
                        <Num value={null} />
                      ))}
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="net-kandidatov">
          Подбор похожих появится вместе с ручкой подбора: сейчас бэкенд кандидатов не
          отдаёт.
        </p>
      )}

      <div className="deystviya">
        {/* Ручек записи ещё нет, и выдумывать их нельзя. */}
        {deystviya.map((deystvie) => (
          <button key={deystvie} type="button" disabled>
            {deystvie}
          </button>
        ))}
        <span className="deystviya-poyasnenie">Действие появится в следующей фазе</span>
      </div>
    </article>
  )
}
