import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { ApiError, api } from './client'
import { sPovtoramiPriObryve } from './kartochki'
import type {
  ConfirmedPair,
  ReferenceForm,
  ReferenceRowPreview,
  ReferenceTransfer,
} from './types'

/*
 * Действия «Сверки справочника»: «Это он», строка ING для формы и перенос
 * карточки в справочник. Приёмы — те же, что у отправки карточки
 * (`api/kartochki.ts`): срок у каждого запроса, повтор после обрыва,
 * защита от двойного нажатия.
 */

const JSON_ZAGOLOVKI = { 'Content-Type': 'application/json' }

/** «Это он» — только база: сервер отвечает за секунды, дольше 10 с не ждёт. */
const SROK_PODTVERZHDENIYA = 20_000
/** Строка ING — свежее чтение двух Google-таблиц: обычно несколько секунд. */
const SROK_PREDPROSMOTRA = 60_000
/**
 * Одна попытка переноса. В худшем случае он идёт десятки секунд: очередь
 * писателей до 30 с, чтения и запись Google, перенос книги кухни в базу.
 * 120 с, а не 60: при медленном Google повтор пришёл бы, пока первый
 * запрос ещё пишет, и получил бы «Таблица занята».
 */
export const SROK_PERENOSA = 120_000
/** Сколько раз перенос повторяется сам после обрыва связи или срока. */
export const POVTOROV_PERENOSA = 2

const KLYUCH_SVERKI = ['reconciliation'] as const
const KLYUCH_SPRAVOCHNIKA = ['ingredients'] as const

/** После отказа, говорящего, что список «Сверки» устарел: карточки нет,
 *  кандидаты сменились, карточку сняли с согласования. */
function perechitatSverku(queries: QueryClient): void {
  void queries.invalidateQueries({ queryKey: KLYUCH_SVERKI })
}

function kod(oshibka: unknown): number | null {
  return oshibka instanceof ApiError ? oshibka.status : null
}

/** Пара подтверждена или карточка в справочнике: она ушла со «Сверки», а в
 *  справочнике у ингредиента появилась карточка (или он сам). */
function perechitatPosleUspekha(queries: QueryClient): void {
  void queries.invalidateQueries({ queryKey: KLYUCH_SVERKI })
  void queries.invalidateQueries({ queryKey: KLYUCH_SPRAVOCHNIKA })
}

/**
 * Строка ING карточки — для формы переноса.
 *
 * Каждое чтение — запросы к Google, поэтому только при открытии формы и по
 * «Проверить ещё раз»: не при возврате во вкладку и не по строке свежести
 * (ключ не под `reconciliation` — его префикс она перечитывает). Закрытая
 * форма кэш не держит: открытая снова читает свежую строку. Повтора нет —
 * его делает человек кнопкой.
 */
export function useStrokaSpravochnika(cardId: number): UseQueryResult<ReferenceRowPreview> {
  const queries = useQueryClient()
  return useQuery({
    queryKey: ['svarka', 'stroka', cardId],
    queryFn: async ({ signal }) => {
      try {
        return await api<ReferenceRowPreview>(`/reconciliation/${cardId}/reference-row`, {
          signal,
          srok: SROK_PREDPROSMOTRA,
        })
      } catch (oshibka) {
        if (kod(oshibka) === 404) perechitatSverku(queries)
        throw oshibka
      }
    },
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}

/**
 * «Это он»: выбранный кандидат становится парой карточки. Только база,
 * таблица не меняется. Повтор безопасен: сервер ответит «уже».
 *
 * Двойное нажатие даёт один запрос — не только серой кнопкой: два касания
 * успевают раньше перерисовки.
 */
export function usePodtverzhdenie(onGotovo: (para: ConfirmedPair) => void) {
  const queries = useQueryClient()
  const idyot = useRef(false)
  const mutatsiya = useMutation({
    mutationFn: ({ cardId, ingredientId }: { cardId: number; ingredientId: number }) =>
      api<ConfirmedPair>(`/reconciliation/${cardId}/confirm`, {
        method: 'POST',
        headers: JSON_ZAGOLOVKI,
        body: JSON.stringify({ ingredient_id: ingredientId }),
        srok: SROK_PODTVERZHDENIYA,
      }),
    onSuccess: (para) => {
      perechitatPosleUspekha(queries)
      onGotovo(para)
    },
    // 404 — карточки нет; 409 — этого ингредиента нет среди кандидатов:
    // кандидаты сменились, список надо перечитать.
    onError: (oshibka) => {
      if (kod(oshibka) === 404 || kod(oshibka) === 409) perechitatSverku(queries)
    },
    onSettled: () => {
      idyot.current = false
    },
  })
  return {
    podtverdit: (cardId: number, ingredientId: number) => {
      if (idyot.current) return
      idyot.current = true
      mutatsiya.mutate({ cardId, ingredientId })
    },
    idyot: mutatsiya.isPending,
    oshibka: mutatsiya.error,
  }
}

/** Одна попытка переноса. Ответ 200, оборванный посреди тела, — обрыв
 *  связи, а не отказ: запись, скорее всего, легла, и повтор её покажет. */
async function popytkaPerenosa(cardId: number, forma: ReferenceForm): Promise<ReferenceTransfer> {
  try {
    return await api<ReferenceTransfer>(`/reconciliation/${cardId}/to-reference`, {
      method: 'POST',
      headers: JSON_ZAGOLOVKI,
      body: JSON.stringify(forma),
      srok: SROK_PERENOSA,
    })
  } catch (oshibka) {
    if (oshibka instanceof ApiError && oshibka.status >= 200 && oshibka.status < 300) {
      throw new ApiError(0, 'Ответ не дошёл целиком')
    }
    throw oshibka
  }
}

/**
 * «Добавить в справочник» («Это новый», «Связать с карточкой»).
 *
 * Перенос идемпотентен: ключ журнала на сервере один на карточку, и повтор
 * отвечает той же строкой — второй записи и второго id не будет. Поэтому
 * обрыв связи и срок без ответа (120 с) — повод повторить тот же запрос
 * самим, до двух раз. Отказ сервера не повторяется: «таблица занята»,
 * «строка ещё не появилась» повтор через 2 с не вылечит — решает человек.
 *
 * Двойное нажатие даёт один запрос. Успех: карточка ушла со «Сверки», в
 * справочнике новый ингредиент — оба списка перечитываются.
 */
export function usePerenos(cardId: number, onZapisano: (otvet: ReferenceTransfer) => void) {
  const queries = useQueryClient()
  const [popytka, zadatPopytku] = useState(1)
  const idyot = useRef(false)
  const mutatsiya = useMutation({
    onMutate: () => zadatPopytku(1),
    mutationFn: (forma: ReferenceForm) =>
      sPovtoramiPriObryve(() => popytkaPerenosa(cardId, forma), POVTOROV_PERENOSA, zadatPopytku),
    onSuccess: (otvet) => {
      perechitatPosleUspekha(queries)
      onZapisano(otvet)
    },
    // 404 — карточки нет; «не „Да“» — согласование сняли после переноса
    // книги карточек: в списке она ещё с кнопкой.
    onError: (oshibka) => {
      const neSoglasovana = oshibka instanceof ApiError && oshibka.reason === 'not_approved'
      if (kod(oshibka) === 404 || neSoglasovana) perechitatSverku(queries)
    },
    onSettled: () => {
      idyot.current = false
    },
  })
  return {
    zapisat: (forma: ReferenceForm) => {
      if (idyot.current) return
      idyot.current = true
      mutatsiya.mutate(forma)
    },
    /** Запись идёт — кнопки ждут. */
    idyot: mutatsiya.isPending,
    /** Номер идущей попытки: 2 и 3 — повтор после обрыва или срока. */
    popytka,
    oshibka: mutatsiya.error,
    /** Забыть прежний отказ: «Проверить ещё раз» начинает заново. */
    sbrosit: mutatsiya.reset,
  }
}
