import { LIMIT_SPRAVOCHNIKA, useIngredients } from '../api/queries'
import type { Ingredient } from '../api/types'
import { estNet } from '../domain/tablitsa'
import type { Column } from '../ui/DataTable'
import { Num } from '../ui/Num'
import { estDannye, SboyObnovleniya, Sostoyanie } from '../ui/Sostoyanie'
import { TablitsaSFiltrami } from '../ui/TablitsaSFiltrami'
import './pages.css'

// Статус — строка из таблицы шефа, а не перечисление: бывает и «архив», и
// «архивный». К одному слову не привязываемся.
function arhivnyy(r: Ingredient): boolean {
  return r.status.startsWith('архив')
}

/**
 * Колонки справочника. Константа модуля: от неё зависят состояние таблицы
 * из адреса и пересчёт строк (`TablitsaSFiltrami`), новый массив на каждую
 * отрисовку пересчитывал бы их каждый раз.
 */
const KOLONKI_INGREDIENTOV: Column<Ingredient>[] = [
  {
    key: 'id',
    title: 'id',
    priority: 'wide',
    // Текстом, но числа внутри — как числа: «12» < «123» < «1000». id —
    // номера, поэтому и подписи числовые, а не «от А до Я».
    sort: { vid: 'tekst', znachenie: (r) => r.legacy_id, podpisi: ['по возрастанию', 'по убыванию'] },
    render: (r) => r.legacy_id,
  },
  {
    key: 'name',
    title: 'Наименование',
    priority: 'always',
    sort: { vid: 'tekst', znachenie: (r) => r.name },
    render: (r) => <span className={arhivnyy(r) ? 'arhivnyy' : undefined}>{r.name}</span>,
  },
  {
    key: 'category',
    title: 'Категория',
    priority: 'wide',
    sort: { vid: 'tekst', znachenie: (r) => r.category },
    filtr: { vid: 'znacheniya', znachenie: (r) => r.category },
    render: (r) => r.category,
  },
  {
    key: 'unit',
    title: 'Ед.',
    priority: 'wide',
    // Цена бывает за килограмм и за штуку: единица — отдельной колонкой,
    // чтобы и отобрать по ней, и сравнивать цены одного рода.
    sort: { vid: 'tekst', znachenie: (r) => r.unit },
    filtr: { vid: 'znacheniya', znachenie: (r) => r.unit },
    render: (r) => r.unit,
  },
  {
    key: 'price',
    title: 'Цена за единицу',
    align: 'right',
    priority: 'always',
    sort: { vid: 'chislo', znachenie: (r) => r.price_per_kg },
    filtr: estNet((r) => r.price_per_kg !== null),
    // Имя поля price_per_kg врёт: при unit «шт» это цена за штуку.
    // Подпись — «Цена за единицу», сама единица берётся из unit.
    render: (r) => <Num value={r.price_per_kg} unit={`₽/${r.unit}`} />,
  },
  {
    key: 'ves',
    title: 'Вес 1 шт',
    align: 'right',
    priority: 'wide',
    sort: { vid: 'chislo', znachenie: (r) => r.weight_per_piece_g },
    filtr: estNet((r) => r.weight_per_piece_g !== null),
    // У весовых weight_per_piece_g — null, и это норма, а не пропуск:
    // Num отрисует приглушённый прочерк, отличимый от нуля.
    render: (r) => <Num value={r.weight_per_piece_g} unit="г" />,
  },
  {
    key: 'status',
    title: 'Статус',
    priority: 'wide',
    sort: { vid: 'tekst', znachenie: (r) => r.status },
    // Значения — из ответа: список статусов в коде не зашит.
    filtr: { vid: 'znacheniya', znachenie: (r) => r.status },
    render: (r) => r.status,
  },
  {
    key: 'card',
    title: 'Карточка',
    priority: 'always',
    // По возрастанию — сначала позиции без карточки: их и ищут.
    sort: {
      vid: 'chislo',
      znachenie: (r) => (r.has_card ? 1 : 0),
      podpisi: ['сначала без карточки', 'сначала с карточкой'],
    },
    filtr: estNet((r) => r.has_card),
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

// Константа модуля по той же причине, что и колонки.
function poiskPo(r: Ingredient): readonly string[] {
  return [r.name, r.legacy_id]
}

export function Ingredients() {
  // Весь справочник одним запросом; поиск, сортировка и фильтры — в
  // браузере и в адресе (TablitsaSFiltrami): ссылку на отобранный список
  // шеф шлёт в переписке, и она открывается тем же видом.
  const query = useIngredients()

  return (
    <section>
      <h1>Справочник ингредиентов</h1>
      {/* Таблица — только когда справочник пришёл: пустой список до ответа
          показал бы «Ингредиентов пока нет». */}
      {estDannye(query) ? (
        <>
          <SboyObnovleniya query={query} />
          {/* Ответ длиной в предел — почти наверняка обрезан. Молчать нельзя:
              поиск не нашёл бы позицию, не поместившуюся в ответ, и шеф
              решил бы, что её нет в справочнике. Чинит это разработчик
              (предел ручки), поэтому текст и говорит, к кому идти. */}
          {query.data.length >= LIMIT_SPRAVOCHNIKA && (
            <p className="spravochnik-obrezan" role="status">
              В справочнике больше {LIMIT_SPRAVOCHNIKA} позиций, а на экране только первые{' '}
              {LIMIT_SPRAVOCHNIKA} — нужной позиции может не оказаться в списке. Сообщите разработчику.
            </p>
          )}
          <TablitsaSFiltrami
            stroki={query.data}
            kolonki={KOLONKI_INGREDIENTOV}
            poiskPo={poiskPo}
            rowKey={(r) => String(r.id)}
            empty="Ингредиентов пока нет"
          />
        </>
      ) : (
        <Sostoyanie query={query} />
      )}
    </section>
  )
}
