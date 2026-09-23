import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { api } from './client'
import type { Dish, DishDetail, Ingredient, Me, Reconciliation } from './types'

export function useMe(): UseQueryResult<Me> {
  return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
}

function stroka(params: Record<string, string>): string {
  const chistye = Object.entries(params).filter(([, v]) => v !== '')
  return chistye.length ? `?${new URLSearchParams(chistye).toString()}` : ''
}

export function useIngredients(search = '', status = ''): UseQueryResult<Ingredient[]> {
  return useQuery({
    queryKey: ['ingredients', search, status],
    queryFn: () => api<Ingredient[]>(`/ingredients${stroka({ search, status })}`),
  })
}

export function useDishes(search = '', status = ''): UseQueryResult<Dish[]> {
  return useQuery({
    queryKey: ['dishes', search, status],
    queryFn: () => api<Dish[]>(`/dishes${stroka({ search, status })}`),
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
