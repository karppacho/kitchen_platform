import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { api } from './client'
import type { Dish, DishDetail, Ingredient, Me, Reconciliation, SyncStatus } from './types'

export function useMe(): UseQueryResult<Me> {
  return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
}

/**
 * Справочник берётся целиком, одним ответом: поиск, сортировка и фильтры
 * работают в браузере по всем строкам. Без явного предела ручка отдаёт 200
 * строк, 500 — её максимум; ингредиентов сейчас около 130. Упрётся ответ в
 * предел — экран справочника об этом скажет.
 */
export const LIMIT_SPRAVOCHNIKA = 500

/**
 * Поиска и фильтров в ключе нет: набор в поиске не шлёт запросов и не
 * прячет таблицу за «Загрузкой…». Префикс `ingredients` — по нему строка
 * свежести перезапрашивает экран; ключ один и у справочника, и у сверки —
 * кэш общий.
 */
export const KLYUCH_INGREDIENTOV = ['ingredients', { limit: LIMIT_SPRAVOCHNIKA }] as const

export function useIngredients(): UseQueryResult<Ingredient[]> {
  return useQuery({
    queryKey: KLYUCH_INGREDIENTOV,
    queryFn: () => api<Ingredient[]>(`/ingredients?limit=${LIMIT_SPRAVOCHNIKA}`),
  })
}

/**
 * Блюда — тоже целиком, одним ответом: предела у ручки нет, блюд около 130.
 * Поиска и статуса в ключе нет, как у справочника; префикс `dishes` — по
 * нему строка свежести перезапрашивает экран.
 */
export const KLYUCH_BLYUD = ['dishes'] as const

export function useDishes(): UseQueryResult<Dish[]> {
  return useQuery({
    queryKey: KLYUCH_BLYUD,
    queryFn: () => api<Dish[]>('/dishes'),
  })
}

export function useDish(legacyId: string): UseQueryResult<DishDetail> {
  return useQuery({
    queryKey: ['dish', legacyId],
    queryFn: () => api<DishDetail>(`/dishes/${encodeURIComponent(legacyId)}`),
  })
}

export function useReconciliation(): UseQueryResult<Reconciliation> {
  return useQuery({
    queryKey: ['reconciliation'],
    queryFn: () => api<Reconciliation>('/reconciliation'),
  })
}

export function useSync(): UseQueryResult<SyncStatus> {
  return useQuery({
    queryKey: ['sync'],
    queryFn: () => api<SyncStatus>('/sync'),
    // Синхронизация — раз в пять минут; строка отстаёт от неё не больше чем на минуту.
    refetchInterval: 60_000,
  })
}
