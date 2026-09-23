import { ApiError } from '../api/client'
import './sostoyanie.css'

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

function otkazPoPravam(oshibka: unknown): boolean {
  return oshibka instanceof ApiError && oshibka.status === 403
}

// Различаем по status, а не по тексту: обрыв сети — status 0, 5xx —
// «сервер ответил, но не смог отдать данные».
function zagolovokSboya(oshibka: unknown): string {
  if (oshibka instanceof ApiError && oshibka.status === 0) return 'Нет связи с сервером'
  if (otkazPoPravam(oshibka)) return 'Доступа нет'
  return 'Не удалось получить данные'
}

/**
 * Есть ли что показывать на экране.
 *
 * Данные пришли хотя бы раз — показываем их, даже если последнее фоновое
 * обновление упало: TanStack Query держит прежние данные при status
 * `error`, и они верны, пока не пришли новые. Отказ по правам — другое
 * дело: доступ отозван, и прежние данные больше не показываем.
 */
export function estDannye<Q extends { data: unknown; error: unknown }>(
  query: Q,
): query is Q & { data: NonNullable<Q['data']> } {
  return query.data !== undefined && query.data !== null && !otkazPoPravam(query.error)
}

/**
 * Загрузка и отказ — словами, когда показать нечего.
 *
 * Молчаливо пустой экран запрещён: пустой справочник и недоступный
 * справочник выглядят одинаково, а значат разное.
 *
 * Класс свой, а не `.sboy`: тем классом полноэкранный сбой стартовой
 * проверки сессии (App.tsx), и общий класс растягивал бы сообщение внутри
 * экрана на всю высоту окна.
 */
export function Sostoyanie({ query }: { query: SostoyanieZaprosa }) {
  if (query.isPending) return <p className="zagruzka">Загрузка…</p>
  if (!query.isError) return null

  const oshibka = query.error
  return (
    <div className="sostoyanie" role="alert">
      <p>{zagolovokSboya(oshibka)}</p>
      <p className="sostoyanie-prichina">
        {oshibka instanceof Error ? oshibka.message : 'неизвестная ошибка'}
      </p>
      {/* Повторять запрос, отвергнутый по правам, бессмысленно. */}
      {!otkazPoPravam(oshibka) && (
        <button type="button" onClick={() => void query.refetch()}>
          Повторить
        </button>
      )}
    </div>
  )
}

/**
 * Упало фоновое обновление, а данные на экране есть.
 *
 * Компактной полосой над данными, а не вместо них: шеф вернулся во вкладку
 * на кухонном Wi-Fi, и отнимать у него экран из-за связи незачем. Но и
 * молчать нельзя — на экране то, что было при прошлой загрузке.
 */
export function SboyObnovleniya({ query }: { query: SostoyanieZaprosa }) {
  if (!query.isError) return null
  return (
    <div className="sostoyanie-polosa" role="status">
      <span>
        {zagolovokSboya(query.error)}: не удалось обновить, на экране прежние данные.
      </span>
      <button type="button" onClick={() => void query.refetch()}>
        Повторить
      </button>
    </div>
  )
}
