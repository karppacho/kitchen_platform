import './filtry.css'

type Props = {
  search: string
  onSearch: (znachenie: string) => void
  status: string
  onStatus: (znachenie: string) => void
  /** Значения берутся из ответа, а не зашиваются в код: у ингредиентов
   *  «активный», у блюд «активное» — это данные шефа, а не перечисление. */
  statusy: string[]
  vsego?: number
}

export function Filtry({ search, onSearch, status, onStatus, statusy, vsego }: Props) {
  return (
    <div className="filtry">
      <label className="filtry-pole">
        <span className="vizualno-skryto">Поиск по названию</span>
        <input
          type="search"
          placeholder="Поиск по названию"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </label>
      <label className="filtry-pole">
        <span className="vizualno-skryto">Статус</span>
        <select value={status} onChange={(e) => onStatus(e.target.value)}>
          <option value="">все статусы</option>
          {statusy.map((znachenie) => (
            <option key={znachenie} value={znachenie}>
              {znachenie}
            </option>
          ))}
        </select>
      </label>
      {vsego !== undefined && (
        // aria-live: смена числа найденных строк должна быть слышна
        // программе чтения с экрана, а не только видна зрячему.
        <span className="filtry-schyot" aria-live="polite">
          Найдено: {vsego}
        </span>
      )}
    </div>
  )
}
