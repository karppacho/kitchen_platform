import { ApiError } from '../api/client'

/**
 * Минимальная форма результата запроса, которая нужна для показа состояния.
 *
 * Не `UseQueryResult<unknown>`: обобщённый тип TanStack Query инвариантен
 * из-за `refetch`, и `UseQueryResult<Ingredient[]>` в него не присвоится —
 * `tsc` упадёт на любом конкретном экране. Структурный тип принимает
 * результат любого `useQuery<T>` без приведения типов.
 */
export type SostoyanieZaprosa = {
  isPending: boolean
  isError: boolean
  error: unknown
  refetch: () => unknown
}

/**
 * Загрузка и отказ — словами.
 *
 * Молчаливо пустой экран запрещён: пустой справочник и недоступный
 * справочник выглядят одинаково, а значат разное.
 */
export function Sostoyanie({ query }: { query: SostoyanieZaprosa }) {
  if (query.isPending) return <p className="zagruzka">Загрузка…</p>
  if (!query.isError) return null

  const oshibka = query.error
  const otkaz = oshibka instanceof ApiError && oshibka.status === 403
  // Различаем по status, а не по тексту: обрыв сети — status 0, 5xx —
  // «сервер ответил, но не смог отдать данные».
  const zagolovok =
    oshibka instanceof ApiError && oshibka.status === 0
      ? 'Нет связи с сервером'
      : otkaz
        ? 'Доступа нет'
        : 'Не удалось получить данные'

  return (
    <div className="sboy" role="alert">
      <p>{zagolovok}</p>
      <p className="sboy-prichina">
        {oshibka instanceof Error ? oshibka.message : 'неизвестная ошибка'}
      </p>
      {/* Повторять запрос, отвергнутый по правам, бессмысленно. */}
      {!otkaz && (
        <button type="button" onClick={() => void query.refetch()}>
          Повторить
        </button>
      )}
    </div>
  )
}
