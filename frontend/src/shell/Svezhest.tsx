import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { useSync } from '../api/queries'
import type { SyncBook } from '../api/types'
import { vremyaDannyh } from '../domain/vremya'

// Экраны с данными из таблиц — префиксы ключей из api/queries.ts. Их
// перезапрашиваем, когда синхронизация перенесла изменения.
const EKRANY = [['ingredients'], ['dishes'], ['dish'], ['reconciliation']]

function otstavanie(kniga: SyncBook): string {
  if (kniga.checked_at === null) {
    return `Данные не синхронизировались — ${kniga.title}${kniga.problem ? `: ${kniga.problem}` : ''}`
  }
  const prichina = kniga.problem ?? 'синхронизация не запущена'
  const s = vremyaDannyh(kniga.checked_at)
  return `Данные не обновляются${s === null ? '' : ` с ${s}`} — ${kniga.title}: ${prichina}`
}

/**
 * Насколько свежи данные на экране.
 *
 * Сайт, который расходится с таблицей и молчит, хуже никакого: шеф правит
 * лист и не понимает, почему на сайте старое. Свежо — приглушённая строка.
 * Книга отстала больше чем на 15 минут (считает сервер) — полоса с причиной;
 * её видят все, часть причин шеф устранит сам. Ручка не ответила — строки
 * нет, экраны живут как жили; упал опрос после успешного — остаётся
 * последняя известная строка (время в ней абсолютное, она не врёт).
 */
export function Svezhest() {
  const query = useSync()
  const queries = useQueryClient()
  const prezhnee = useRef<string | null | undefined>(undefined)
  const izmeneno = query.data?.changed_at

  // Сравниваем с прежним значением, а не полагаемся на то, что эффект
  // срабатывает только при смене: React в StrictMode (main.tsx) повторяет
  // эффекты при монтировании, и повтор не должен выглядеть сменой.
  useEffect(() => {
    if (izmeneno === undefined) return
    if (prezhnee.current !== undefined && prezhnee.current !== izmeneno) {
      for (const queryKey of EKRANY) void queries.invalidateQueries({ queryKey })
    }
    prezhnee.current = izmeneno
  }, [izmeneno, queries])

  const dannye = query.data
  const otstavshie = dannye ? dannye.books.filter((kniga) => kniga.stale) : []
  const naVremya =
    dannye && otstavshie.length === 0 && dannye.data_as_of !== null
      ? vremyaDannyh(dannye.data_as_of)
      : null

  // Полоса — живая область, и она есть всегда, даже пустая: программы чтения
  // с экрана объявляют изменения внутри уже существующей области, а область,
  // появившуюся сразу с текстом, часто пропускают. Приглушённая строка — вне
  // области: её время меняется каждые пять минут, объявлять это незачем.
  return (
    <>
      {naVremya !== null && <p className="svezhest">Данные из таблицы на {naVremya}</p>}
      <div className={otstavshie.length > 0 ? 'svezhest svezhest--staro' : undefined} role="status">
        {otstavshie.map((kniga) => (
          <p key={kniga.book}>{otstavanie(kniga)}</p>
        ))}
      </div>
    </>
  )
}
