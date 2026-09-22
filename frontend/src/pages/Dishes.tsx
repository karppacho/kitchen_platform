import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { useDishes } from '../api/queries'
import type { Dish } from '../api/types'
import { dorozhe } from '../domain/tseny'
import { DataTable, type Column } from '../ui/DataTable'
import { Filtry } from '../ui/Filtry'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import { useOtlozhennyiPoisk } from '../ui/useOtlozhennyiPoisk'
import './pages.css'

export function Dishes() {
  // Поиск и фильтр живут в адресе — как и в справочнике ингредиентов:
  // ссылку на отфильтрованный список шеф шлёт в переписке.
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const status = params.get('status') ?? ''
  const idti = useNavigate()

  const [vvod, zadatVvod] = useOtlozhennyiPoisk(search, (znachenie) => {
    const novye = new URLSearchParams(params)
    if (znachenie) novye.set('search', znachenie)
    else novye.delete('search')
    setParams(novye, { replace: true })
  })

  // Поиск — серверный, статус — на клиенте: 130 блюд помещаются в один
  // ответ, и все строки и так уже на руках. Список статусов считаем по
  // ответу, отфильтрованному только поиском, — иначе выбор статуса вычищал
  // бы остальные значения из выпадающего списка (см. Ingredients.tsx).
  const query = useDishes(search)
  const vseStroki = useMemo(() => query.data ?? [], [query.data])
  const statusy = useMemo(() => [...new Set(vseStroki.map((r) => r.status))].sort(), [vseStroki])
  const stroki = useMemo(
    () => (status ? vseStroki.filter((r) => r.status === status) : vseStroki),
    [vseStroki, status],
  )

  const kolonki: Column<Dish>[] = [
    { key: 'id', title: 'id', priority: 'wide', render: (r) => r.legacy_id },
    { key: 'name', title: 'Название', priority: 'always', render: (r) => r.name },
    { key: 'category', title: 'Категория', priority: 'wide', render: (r) => r.category },
    {
      key: 'price',
      title: 'Цена меню',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.price_menu} fraction={2} unit="₽" />,
    },
    {
      key: 'uc',
      title: 'UC ₽',
      align: 'right',
      priority: 'always',
      render: (r) =>
        dorozhe(r.uc_rub, r.price_menu) ? (
          // Подпись направляет к причине: перепутанная единица измерения
          // встречается несравнимо чаще настоящего убытка.
          <span title="Себестоимость выше цены меню — проверьте единицы измерения">
            <Num value={r.uc_rub} fraction={2} unit="₽" />
          </span>
        ) : (
          <Num value={r.uc_rub} fraction={2} unit="₽" />
        ),
    },
    {
      key: 'ucp',
      title: 'UC %',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.uc_percent} fraction={1} unit="%" />,
    },
    {
      key: 'margin',
      title: 'Маржа %',
      align: 'right',
      priority: 'always',
      render: (r) => <Num value={r.margin_percent} fraction={1} unit="%" />,
    },
    {
      key: 'output',
      title: 'Выход',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.output_grams} unit="г" />,
    },
    {
      key: 'warnings',
      title: 'Замечания',
      align: 'right',
      priority: 'wide',
      // Ноль — не «всё хорошо», а «нам не на что указать». Тревогой не
      // красим: замечание не значит поломку.
      render: (r) => (
        <span className={r.warnings > 0 ? 'zamechaniya' : undefined}>{r.warnings}</span>
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
      <h1>Блюда</h1>
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
          rowKey={(r) => r.legacy_id}
          rowClass={(r) => (dorozhe(r.uc_rub, r.price_menu) ? 'stroka--ubytok' : undefined)}
          onOpen={(r) => idti(`/dishes/${r.legacy_id}`)}
          empty="Ничего не найдено"
        />
      )}
    </section>
  )
}
