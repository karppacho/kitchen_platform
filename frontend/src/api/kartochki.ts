import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError, api } from './client'
import type { CardOptions, Draft, DraftPatch, NameCheck, Shag } from './types'

/** Шаги мастера по порядку — те же, что у сервера. По ним «Шаг N из 9». */
export const SHAGI: readonly Shag[] = [
  'supplier',
  'category',
  'name',
  'label',
  'review',
  'approval',
  'photos',
  'description',
  'summary',
]

export const NAZVANIYA_SHAGOV: Record<Shag, string> = {
  supplier: 'Поставщик',
  category: 'Категория',
  name: 'Название',
  label: 'Фото этикетки',
  review: 'Проверка',
  approval: 'Согласован ли продукт',
  photos: 'Фото продукта',
  description: 'Описание',
  summary: 'Итог',
}

/** Черновик — один на повара, поэтому и ключ один. Ответ каждой правки
 *  кладётся сюда же: шаг на экране — всегда шаг черновика на сервере. */
export const KLYUCH_CHERNOVIKA = ['kartochki', 'chernovik'] as const

const JSON_ZAGOLOVKI = { 'Content-Type': 'application/json' }

export function useChernovik(): UseQueryResult<Draft | null> {
  return useQuery({
    queryKey: KLYUCH_CHERNOVIKA,
    queryFn: () => api<Draft | null>('/cards/drafts/current'),
  })
}

export function useVarianty(): UseQueryResult<CardOptions> {
  return useQuery({
    queryKey: ['kartochki', 'varianty'],
    queryFn: () => api<CardOptions>('/cards/options'),
  })
}

/** Проверка названия. Пустое не проверяется: сервер ответил бы «ничего». */
export function useProverkaImeni(imya: string): UseQueryResult<NameCheck> {
  return useQuery({
    queryKey: ['kartochki', 'imya', imya],
    queryFn: () => api<NameCheck>(`/cards/name-check?name=${encodeURIComponent(imya)}`),
    enabled: imya !== '',
  })
}

/**
 * Отказы, после которых экран отстал от сервера и черновик надо перечитать.
 *
 * 404 — черновика больше нет: его отправили или сбросили в другой вкладке.
 * Перечитав, экран сам покажет «Начать» вместо тупика «обновите страницу».
 * 401 — продление не помогло: перечитывание пройдёт через кэш запросов, и
 * оболочка вернёт на форму входа так же, как с любого экрана, — правки
 * идут мимо этого кэша и сами сессию не сбрасывают.
 */
const OTSTALI = [401, 404]

/** У начала есть ещё 409: черновик уже завели (вторая вкладка, двойное
 *  касание) — перечитав, повар выберет «Продолжить» или «Начать заново».
 *  409 при удалении (идёт отправка) перечитывание не меняет: черновик тот
 *  же, текст сервера остаётся на экране, сами не повторяем. */
const OTSTALI_PRI_NACHALE = [...OTSTALI, 409]

function perechitatPosle(queries: QueryClient, oshibka: unknown, kody: readonly number[]): void {
  if (oshibka instanceof ApiError && kody.includes(oshibka.status)) {
    void queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })
  }
}

/**
 * Перед правкой — остановить идущее перечитывание черновика. Телефон
 * перечитывает его, когда повар возвращается во вкладку; ответ, ушедший до
 * правки и пришедший после, лёг бы поверх ответа правки — и экран вернулся
 * бы на шаг назад.
 */
function ostanovitPerechityvanie(queries: QueryClient): Promise<void> {
  return queries.cancelQueries({ queryKey: KLYUCH_CHERNOVIKA })
}

/** Правка черновика. Ответ — черновик целиком: он и становится экраном. */
export function usePravkaChernovika() {
  const queries = useQueryClient()
  return useMutation({
    onMutate: () => ostanovitPerechityvanie(queries),
    mutationFn: ({ id, pravka }: { id: string; pravka: DraftPatch }) =>
      api<Draft>(`/cards/drafts/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: JSON_ZAGOLOVKI,
        body: JSON.stringify(pravka),
      }),
    onSuccess: (chernovik) => queries.setQueryData(KLYUCH_CHERNOVIKA, chernovik),
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI),
  })
}

/**
 * «Начать» и «Начать заново» — одним действием, чтобы у них был один отказ
 * на экране. У «заново» — id прежнего черновика: он удаляется (его фото
 * уходят в корзину), и сразу заводится новый.
 *
 * 404 при удалении — черновика уже нет (отправлен или сброшен в другой
 * вкладке): цель та же, заводим новый. Удаление прошло, а новый не завёлся
 * (обрыв связи) — в кэш кладётся «черновика нет»: экран покажет «Начать», а
 * не удалённый черновик, правка которого ответила бы 404.
 *
 * `onNachato` зовётся в одном вызове с записью в кэш: экран узнаёт, что
 * повар уже в работе, в ту же отрисовку, что и новый черновик, — без
 * мелькания «Продолжить или начать заново?» над только что начатым.
 */
export function useNachatChernovik(onNachato: (chernovik: Draft) => void) {
  const queries = useQueryClient()
  return useMutation({
    onMutate: () => ostanovitPerechityvanie(queries),
    mutationFn: async (prezhniy: string | null): Promise<Draft> => {
      if (prezhniy !== null) {
        try {
          await api<void>(`/cards/drafts/${encodeURIComponent(prezhniy)}`, { method: 'DELETE' })
        } catch (oshibka) {
          if (!(oshibka instanceof ApiError && oshibka.status === 404)) throw oshibka
        }
      }
      try {
        return await api<Draft>('/cards/drafts', { method: 'POST' })
      } catch (oshibka) {
        if (prezhniy !== null) queries.setQueryData(KLYUCH_CHERNOVIKA, null)
        throw oshibka
      }
    },
    onSuccess: (chernovik) => {
      onNachato(chernovik)
      queries.setQueryData(KLYUCH_CHERNOVIKA, chernovik)
    },
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI_PRI_NACHALE),
  })
}
