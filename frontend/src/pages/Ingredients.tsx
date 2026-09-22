import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useIngredients } from '../api/queries'
import type { Ingredient } from '../api/types'
import { DataTable, type Column } from '../ui/DataTable'
import { Filtry } from '../ui/Filtry'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'

// Пауза после последней буквы, прежде чем запрос уйдёт на сервер. На каждую
// букву слать нельзя — 130 строк и печатающий шеф дадут запрос на каждый
// символ; но и заставлять ждать секунду после того, как он замер, тоже
// раздражает. 300 мс — между «сразу» и «через раздумье».
const ZADERZHKA_POISKA = 300

export function Ingredients() {
  // Поиск и фильтр живут в адресе: ссылку на отфильтрованный список шеф
  // шлёт в переписке, и она должна открываться тем же экраном, без
  // необходимости набирать всё заново.
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const status = params.get('status') ?? ''

  // Локальный ввод — чтобы поле не залипало на каждой букве, ожидая
  // подтверждения от адресной строки; в адрес значение уходит с задержкой.
  const [vvod, zadatVvod] = useState(search)

  // Если адрес поменялся не из этого поля (открыли присланную ссылку,
  // нажали «назад») — подхватываем значение в поле ввода.
  useEffect(() => {
    zadatVvod(search)
  }, [search])

  useEffect(() => {
    if (vvod === search) return
    const taimer = setTimeout(() => {
      const novye = new URLSearchParams(params)
      if (vvod) novye.set('search', vvod)
      else novye.delete('search')
      setParams(novye, { replace: true })
    }, ZADERZHKA_POISKA)
    return () => clearTimeout(taimer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vvod])

  const query = useIngredients(search, status)
  const stroki = useMemo(() => query.data ?? [], [query.data])
  const statusy = useMemo(() => [...new Set(stroki.map((r) => r.status))].sort(), [stroki])

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
