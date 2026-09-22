import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useIngredients } from '../api/queries'
import type { Ingredient } from '../api/types'
import { DataTable, type Column } from '../ui/DataTable'
import { Filtry } from '../ui/Filtry'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import { useOtlozhennyiPoisk } from '../ui/useOtlozhennyiPoisk'
import './pages.css'

export function Ingredients() {
  // Поиск и фильтр живут в адресе: ссылку на отфильтрованный список шеф
  // шлёт в переписке, и она должна открываться тем же экраном, без
  // необходимости набирать всё заново.
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const status = params.get('status') ?? ''

  const [vvod, zadatVvod] = useOtlozhennyiPoisk(search, (znachenie) => {
    const novye = new URLSearchParams(params)
    if (znachenie) novye.set('search', znachenie)
    else novye.delete('search')
    setParams(novye, { replace: true })
  })

  // Поиск остаётся серверным (там ilike по имени — трогать незачем), а
  // статус фильтруем на клиенте: справочник целиком помещается в один
  // ответ (лимит бэкенда по умолчанию — 200 строк, у ингредиентов их 130),
  // все строки и так уже на руках. Если бы фильтр по статусу уходил на
  // сервер вместе с поиском, список статусов в Filtry приходилось бы
  // считать из уже отфильтрованного ответа — тогда выбор «архивный» стирал
  // бы «активный» из выпадающего списка, и вернуться можно было бы только
  // сбросом. Считаем statusy по полному (лишь отфильтрованному поиском)
  // ответу, а саму таблицу — по нему же плюс статус.
  // Если справочник вырастет за лимит бэкенда, это решение придётся
  // пересмотреть — грузить статус целиком тогда будет нельзя.
  const query = useIngredients(search)
  const vseStroki = useMemo(() => query.data ?? [], [query.data])
  const statusy = useMemo(() => [...new Set(vseStroki.map((r) => r.status))].sort(), [vseStroki])
  const stroki = useMemo(
    () => (status ? vseStroki.filter((r) => r.status === status) : vseStroki),
    [vseStroki, status],
  )

  const kolonki: Column<Ingredient>[] = [
    { key: 'id', title: 'id', priority: 'wide', render: (r) => r.legacy_id },
    {
      key: 'name',
      title: 'Наименование',
      priority: 'always',
      render: (r) => (
        <span className={r.status.startsWith('архив') ? 'arhivnyy' : undefined}>{r.name}</span>
      ),
    },
    { key: 'category', title: 'Категория', priority: 'wide', render: (r) => r.category },
    {
      key: 'price',
      title: 'Цена за единицу',
      align: 'right',
      priority: 'always',
      // Имя поля price_per_kg врёт: при unit «шт» это цена за штуку.
      // Подпись — «Цена за единицу», сама единица берётся из unit.
      render: (r) => <Num value={r.price_per_kg} unit={`₽/${r.unit}`} />,
    },
    {
      key: 'ves',
      title: 'Вес 1 шт',
      align: 'right',
      priority: 'wide',
      // У весовых weight_per_piece_g — null, и это норма, а не пропуск:
      // Num отрисует приглушённый прочерк, отличимый от нуля.
      render: (r) => <Num value={r.weight_per_piece_g} unit="г" />,
    },
    { key: 'status', title: 'Статус', priority: 'wide', render: (r) => r.status },
    {
      key: 'card',
      title: 'Карточка',
      priority: 'always',
      render: (r) =>
        r.has_card ? (
          <span aria-label="карточка есть" title="карточка есть">
            ✓
          </span>
        ) : (
          // Отсутствие карточки — сигнал: позиция справочника есть, а
          // карточки от повара нет.
          <span aria-label="карточки нет" title="карточки нет" className="net-kartochki">
            ○
          </span>
        ),
    },
  ]

  function zadatStatus(znachenie: string) {
    const novye = new URLSearchParams(params)
    if (znachenie) novye.set('status', znachenie)
    else novye.delete('status')
    setParams(novye, { replace: true })
  }

  return (
    <section>
      <h1>Справочник ингредиентов</h1>
      <Filtry
        search={vvod}
        onSearch={zadatVvod}
        status={status}
        onStatus={zadatStatus}
        statusy={statusy}
        vsego={query.isSuccess ? stroki.length : undefined}
      />
      <Sostoyanie query={query} />
      {query.isSuccess && (
        <DataTable
          columns={kolonki}
          rows={stroki}
          rowKey={(r) => String(r.id)}
          empty="Ничего не найдено"
        />
      )}
    </section>
  )
}
