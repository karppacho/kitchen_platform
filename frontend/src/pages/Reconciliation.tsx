import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, NE_POLUCHILOS } from '../api/client'
import { useIngredients, useReconciliation } from '../api/queries'
import { usePodtverzhdenie } from '../api/svarka'
import type { Ingredient, LinkStatus, ReconciliationRow } from '../api/types'
import { NameDiff } from '../ui/NameDiff'
import { Num } from '../ui/Num'
import { estDannye, SboyObnovleniya, Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'
import './sverka.css'
import { FormaSpravochnika } from './svarka/FormaSpravochnika'
import './svarka/formaSpravochnika.css'

// Три группы требуют разных действий (решение 7 спеки этапа 6): выбрать из
// тёзок, подтвердить похожее — или признать новым и добавить в справочник.
const GRUPPY: { status: LinkStatus; zagolovok: string; chto: string }[] = [
  {
    status: 'ambiguous',
    zagolovok: 'Несколько совпадений',
    chto:
      'В справочнике несколько позиций с этим именем. Выберите, какая имелась в виду, — ' +
      'различает их не имя, а цена и единица — и нажмите «Это он». Если ни одна — ' +
      '«Это новый».',
  },
  {
    status: 'candidate',
    zagolovok: 'Есть похожее',
    chto:
      'Точного совпадения нет. Если похожее — то самое, выберите его и нажмите «Это он»; ' +
      'если в справочнике такого нет — «Это новый».',
  },
  {
    status: 'orphan',
    zagolovok: 'Пары нет',
    chto:
      'Карточка оформлена, но калькулятор её не видит. «Добавить в справочник» заполнит ' +
      'её строку в листе ING — id, цену, единицу и потери.',
  },
]

/** Текст сервера (спека, «Ответы человеку»): в справочник попадают только «Да». */
const NE_SOGLASOVANA = 'Карточка не согласована — в справочник попадают только «Да»'

/** Что сказать об отказе «Это он». Текст сервера — как есть («обновите
 *  страницу», «справочник обновляется»); свой — только когда сервер ничего
 *  не сказал. Выбор остаётся — нажать можно ещё раз. */
function tekstOtkazaPary(oshibka: Error): string {
  if (oshibka instanceof ApiError && oshibka.status >= 500 && oshibka.message === NE_POLUCHILOS) {
    return 'Сервер не ответил — нажмите «Это он» ещё раз через минуту.'
  }
  return oshibka.message
}

/** Итог последнего действия: карточка ушла со «Сверки», а сказать, что
 *  случилось, надо. */
type Itog = { tekst: string; zametki: readonly string[]; vnimanie: boolean }

export function Reconciliation() {
  const query = useReconciliation()
  // Список справочника берётся один раз и держится под рукой: ручка сверки
  // отдаёт у кандидатов только id и имя, а выбирают по цене.
  const spravochnik = useIngredients()
  // Форма переноса — одна на экран; кнопка, которая её открыла, получит
  // фокус обратно, когда форму закроют.
  const [forma, zadatFormu] = useState<ReconciliationRow | null>(null)
  const otkryvshaya = useRef<HTMLElement | null>(null)
  const [itog, zadatItog] = useState<Itog | null>(null)
  const itogRef = useRef<HTMLDivElement>(null)

  // Кнопка, на которой был фокус, ушла вместе с карточкой — фокус на итог.
  useEffect(() => {
    if (itog) itogRef.current?.focus()
  }, [itog])

  // null — справочник ещё не пришёл: прочерк вместо цены соврал бы, что
  // цены нет вовсе.
  const poId = useMemo(() => {
    if (!spravochnik.data) return null
    return new Map(spravochnik.data.map((stroka) => [stroka.id, stroka]))
  }, [spravochnik.data])

  if (!estDannye(query)) return <Sostoyanie query={query} />
  const svodka = query.data

  const otkrytFormu = (stroka: ReconciliationRow, knopka: HTMLElement) => {
    otkryvshaya.current = knopka
    zadatFormu(stroka)
  }
  const zakrytFormu = () => {
    zadatFormu(null)
    otkryvshaya.current?.focus()
  }

  return (
    <section>
      <h1>Сверка справочника</h1>
      <SboyObnovleniya query={query} />
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

      <div role="status">
        {itog && (
          <div
            ref={itogRef}
            tabIndex={-1}
            className={itog.vnimanie ? 'svarka-itog svarka-itog--vnimanie' : 'svarka-itog'}
          >
            <p>{itog.tekst}</p>
            {itog.zametki.length > 0 && (
              <ul>
                {itog.zametki.map((zametka) => (
                  <li key={zametka}>{zametka}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Цены уже пришли раньше — они на экране; говорить приходится только
          о том, что их нет вовсе. */}
      {spravochnik.isError && !estDannye(spravochnik) && (
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
                poId={poId}
                onPerenos={otkrytFormu}
                onPodtverzhdeno={(tekst) => zadatItog({ tekst, zametki: [], vnimanie: false })}
              />
            ))}
          </div>
        )
      })}

      {forma && (
        <FormaSpravochnika
          // Другая карточка — другая форма: набранное для прежней не
          // переносится.
          key={forma.card_id}
          stroka={forma}
          onZakryt={zakrytFormu}
          onZapisano={(otvet) => {
            zadatFormu(null)
            zadatItog({
              tekst: otvet.message,
              zametki: otvet.notes,
              // Не на сайте, пара не подтверждена, прежнюю попытку надо
              // показать шефу — заметно, но не тревогой.
              vnimanie: !otvet.imported || !otvet.linked || otvet.shifted !== null,
            })
          }}
        />
      )}
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

/**
 * Карточка, требующая решения, и что с ней можно сделать — как разрешил
 * сервер (`actions`): «Это он» — выбрать кандидата; «Это новый» или
 * «Добавить в справочник» — форма переноса. Не согласованной в справочник
 * нельзя — вместо кнопки сказано почему.
 */
function Kartochka({
  stroka,
  poId,
  onPerenos,
  onPodtverzhdeno,
}: {
  stroka: ReconciliationRow
  poId: Map<number, Ingredient> | null
  onPerenos: (stroka: ReconciliationRow, knopka: HTMLElement) => void
  onPodtverzhdeno: (tekst: string) => void
}) {
  const [vybor, zadatVybor] = useState<number | null>(null)
  const podtverzhdenie = usePodtverzhdenie((para) => onPodtverzhdeno(para.message))
  // Список перечитан, а выбранного среди кандидатов больше нет — выбора нет.
  const vybrannyi = stroka.candidates.some((k) => k.ingredient_id === vybor) ? vybor : null
  const mozhnoVybrat = stroka.actions.includes('confirm')
  const mozhnoPerenesti = stroka.actions.includes('to_reference')
  const idyot = podtverzhdenie.idyot

  return (
    <article className="sverka-kartochka" data-testid={`kartochka-${stroka.card_id}`}>
      <header>
        {/* Имена показываются как есть, с опечатками и лишними пробелами:
            подчистить за шефа значило бы скрыть, что запись требует
            внимания. */}
        <b>{stroka.name}</b>
        <span className="postavshchik">{stroka.supplier}</span>
      </header>

      {stroka.candidates.length > 0 && (
        <ul className="varianty">
          {stroka.candidates.map((kandidat) => {
            const ingredient = poId?.get(kandidat.ingredient_id)
            return (
              <li key={kandidat.ingredient_id}>
                <label>
                  {/* Предвыбранного варианта нет. Подсвеченное как очевидное
                      совпадение человек примет не глядя. */}
                  <input
                    type="radio"
                    name={`vybor-${stroka.card_id}`}
                    checked={vybrannyi === kandidat.ingredient_id}
                    disabled={!mozhnoVybrat || idyot}
                    onChange={() => zadatVybor(kandidat.ingredient_id)}
                  />
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
      )}

      <div className="deystviya">
        {mozhnoVybrat && (
          <button
            type="button"
            disabled={vybrannyi === null || idyot}
            onClick={() => {
              if (vybrannyi !== null) podtverzhdenie.podtverdit(stroka.card_id, vybrannyi)
            }}
          >
            Это он
          </button>
        )}
        {mozhnoPerenesti && (
          <button
            type="button"
            disabled={idyot}
            onClick={(sobytie) => onPerenos(stroka, sobytie.currentTarget)}
          >
            {stroka.link_status === 'orphan' ? 'Добавить в справочник' : 'Это новый'}
          </button>
        )}
        {!stroka.approved && <span className="svarka-ne-soglasovana">{NE_SOGLASOVANA}</span>}
      </div>

      {podtverzhdenie.oshibka !== null && (
        <p className="svarka-otkaz" role="alert">
          {tekstOtkazaPary(podtverzhdenie.oshibka)}
        </p>
      )}
    </article>
  )
}
