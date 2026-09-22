import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
import type { Me } from '../api/types'

type Sostoyanie = {
  me: Me | null
  loading: boolean
  /** Отказ по правам (403). 401 сюда не попадает — он означает «покажи форму». */
  otkaz: string | null
  /** Стартовую проверку сессии не удалось выполнить (обрыв сети или 5xx).
   *  Это не то же самое, что «не вошёл»: сессия может быть жива, бэкенд
   *  просто не ответил, поэтому форму входа сюда рисовать нельзя. */
  sboy: string | null
  /** Повторить стартовую проверку `/api/me` после sboy. */
  povtorit: () => void
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Kontekst = createContext<Sostoyanie | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [otkaz, setOtkaz] = useState<string | null>(null)
  const [sboy, setSboy] = useState<string | null>(null)
  const [popytka, setPopytka] = useState(0)
  const queryClient = useQueryClient()

  useEffect(() => {
    let zhiv = true
    setLoading(true)
    setOtkaz(null)
    setSboy(null)
    api<Me>('/me')
      .then((profil) => {
        if (zhiv) setMe(profil)
      })
      .catch((oshibka: unknown) => {
        if (!zhiv) return
        if (oshibka instanceof ApiError) {
          // 403 — не повод показывать форму: человек уже представился.
          if (oshibka.status === 403) {
            setOtkaz(oshibka.message)
            return
          }
          // 401 здесь и означает «не вошёл» — этого достаточно, чтобы
          // RequireAuth сам нарисовал форму входа, сохранять нечего.
          if (oshibka.status === 401) return
          // Всё остальное (0, 5xx) — проверку не удалось выполнить. Это не
          // «не вошёл»: отправлять на форму входа при живой сессии нельзя.
          setSboy(oshibka.message)
          return
        }
        setSboy('Не удалось получить данные')
      })
      .finally(() => {
        if (zhiv) setLoading(false)
      })
    return () => {
      zhiv = false
    }
  }, [popytka])

  // Refresh-токен может истечь посреди работы: тогда запрос экрана получит
  // ApiError(401) не через эту стартовую проверку, а через свой useQuery.
  // Единственное общее место заметить это — кэш запросов TanStack Query,
  // через который идут все данные экранов.
  //
  // Смотрим именно на sobytie.action (что произошло только что), а не на
  // sobytie.query.state.error (что лежит в состоянии запроса сейчас): при
  // действии 'fetch' TanStack Query обнуляет error, только если данных ещё
  // не было. У запроса с уже загруженными данными старая ошибка остаётся в
  // state и после успешного повторного запроса — чтение state.error поймало
  // бы её задним числом и сбросило бы свежий, только что открытый вход.
  useEffect(() => {
    const otpiska = queryClient.getQueryCache().subscribe((sobytie) => {
      if (sobytie.type !== 'updated' || sobytie.action.type !== 'error') return
      const oshibka = sobytie.action.error
      if (oshibka instanceof ApiError && oshibka.status === 401) {
        setMe(null)
        // Чужие данные не должны пережить сброс сессии: без очистки кэша
        // они ещё staleTime (60 с) показывались бы без перезапроса — в том
        // числе следующему, кто войдёт с этого же устройства.
        queryClient.clear()
      }
    })
    return otpiska
  }, [queryClient])

  const povtorit = useCallback(() => setPopytka((n) => n + 1), [])

  const login = useCallback(async (email: string, password: string) => {
    const profil = await api<Me>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    setOtkaz(null)
    setSboy(null)
    setMe(profil)
  }, [])

  const logout = useCallback(async () => {
    await api<void>('/auth/logout', { method: 'POST' })
    setMe(null)
    // Тот же повод, что у сброса по 401: следующий, кто войдёт с этого же
    // устройства (общий кухонный планшет), не должен минуту видеть данные
    // предыдущей смены из staleTime.
    queryClient.clear()
  }, [queryClient])

  return (
    <Kontekst.Provider value={{ me, loading, otkaz, sboy, povtorit, login, logout }}>
      {children}
    </Kontekst.Provider>
  )
}

export function useSession(): Sostoyanie {
  const znachenie = useContext(Kontekst)
  if (!znachenie) throw new Error('useSession вне SessionProvider')
  return znachenie
}

/** Названия ролей для шапки. Коды приходят с бэкенда, показывать их человеку
 *  незачем. */
export const ROLI: Record<string, string> = {
  chef: 'бренд-шеф',
  cook: 'повар',
  commerce: 'коммерческий отдел',
  developer: 'разработчик',
}
