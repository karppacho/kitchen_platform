import { useNavigate } from 'react-router-dom'

import { useDishes } from '../api/queries'
import type { Dish } from '../api/types'
import { estNet } from '../domain/tablitsa'
import { dorozhe, PODPIS_DOROZHE } from '../domain/tseny'
import type { Column } from '../ui/DataTable'
import { Num } from '../ui/Num'
import { estDannye, SboyObnovleniya, Sostoyanie } from '../ui/Sostoyanie'
import { TablitsaSFiltrami } from '../ui/TablitsaSFiltrami'
import './pages.css'

/**
 * Колонки списка блюд. Константа модуля: от неё зависят состояние таблицы
 * из адреса и пересчёт строк (`TablitsaSFiltrami`), новый массив на каждую
 * отрисовку пересчитывал бы их каждый раз.
 */
const KOLONKI_BLYUD: Column<Dish>[] = [
  {
    key: 'id',
    title: 'id',
    priority: 'wide',
    // Текстом, но числа внутри — как числа: «B9» < «B10». Подписи — как у
    // id справочника: id — номера, а не слова.
    sort: { vid: 'tekst', znachenie: (r) => r.legacy_id, podpisi: ['по возрастанию', 'по убыванию'] },
    render: (r) => r.legacy_id,
  },
  {
    key: 'name',
    title: 'Название',
    priority: 'always',
    sort: { vid: 'tekst', znachenie: (r) => r.name },
    render: (r) => r.name,
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
    key: 'status',
    title: 'Статус',
    priority: 'wide',
    sort: { vid: 'tekst', znachenie: (r) => r.status },
    // Значения — из ответа: у блюд «активное», у ингредиентов «активный»,
    // список статусов в коде не зашит.
    filtr: { vid: 'znacheniya', znachenie: (r) => r.status },
    render: (r) => r.status,
  },
  {
    key: 'price',
    title: 'Цена меню',
    align: 'right',
    priority: 'wide',
    sort: { vid: 'chislo', znachenie: (r) => r.price_menu },
    filtr: estNet((r) => r.price_menu !== null),
    render: (r) => <Num value={r.price_menu} fraction={2} unit="₽" />,
  },
  {
    key: 'uc',
    title: 'UC ₽',
    align: 'right',
    priority: 'always',
    sort: { vid: 'chislo', znachenie: (r) => r.uc_rub },
    // Одно условие, а не «есть / нет»: себестоимость есть всегда, а искать
    // нужно блюда, где она выше цены, — те же, что окрашены красным.
    filtr: {
      vid: 'usloviya',
      usloviya: [{ kod: 'vyshe', podpis: 'выше цены меню', podhodit: (r) => dorozhe(r.uc_rub, r.price_menu) }],
    },
    render: (r) =>
      dorozhe(r.uc_rub, r.price_menu) ? (
        // Подпись видимым текстом, а не title — на телефоне всплывающей
        // подсказки нет. Направляет к причине: перепутанная единица
        // измерения встречается несравнимо чаще настоящего убытка.
        <>
          <Num value={r.uc_rub} fraction={2} unit="₽" />
          <span className="podpis-dorozhe">{PODPIS_DOROZHE}</span>
        </>
      ) : (
        <Num value={r.uc_rub} fraction={2} unit="₽" />
      ),
  },
  {
    key: 'ucp',
    title: 'UC %',
    align: 'right',
    priority: 'wide',
    sort: { vid: 'chislo', znachenie: (r) => r.uc_percent },
    render: (r) => <Num value={r.uc_percent} fraction={1} unit="%" />,
  },
  {
    key: 'margin',
    title: 'Маржа %',
    align: 'right',
    priority: 'always',
    // По возрастанию — сначала худшая: так шеф и ищет, где теряет. Блюда
    // без цены меню (маржи нет) — в конце в обе стороны.
    sort: {
      vid: 'chislo',
      znachenie: (r) => r.margin_percent,
      podpisi: ['сначала худшая', 'сначала лучшая'],
    },
    render: (r) => <Num value={r.margin_percent} fraction={1} unit="%" />,
  },
  {
    key: 'output',
    title: 'Выход',
    align: 'right',
    priority: 'wide',
    sort: { vid: 'chislo', znachenie: (r) => r.output_grams },
    render: (r) => <Num value={r.output_grams} unit="г" />,
  },
  {
    // Последней колонкой: «Замечания» — итог строки, и тест «ноль
    // замечаний» смотрит именно последнюю ячейку.
    key: 'warnings',
    title: 'Замечания',
    align: 'right',
    priority: 'wide',
    sort: { vid: 'chislo', znachenie: (r) => r.warnings },
    filtr: estNet((r) => r.warnings > 0),
    // Ноль — не «всё хорошо», а «нам не на что указать». Тревогой не
    // красим: замечание не значит поломку.
    render: (r) => (
      <span className={r.warnings > 0 ? 'zamechaniya' : undefined}>{r.warnings}</span>
    ),
  },
]

// Константы модуля по той же причине, что и колонки.
function poiskPo(r: Dish): readonly string[] {
  return [r.name, r.legacy_id]
}

function klyuch(r: Dish): string {
  return r.legacy_id
}

function klassStroki(r: Dish): string | undefined {
  return dorozhe(r.uc_rub, r.price_menu) ? 'stroka--ubytok' : undefined
}

export function Dishes() {
  // Все блюда одним запросом; поиск, сортировка и фильтры — в браузере и в
  // адресе (TablitsaSFiltrami): ссылку на отобранный список шеф шлёт в
  // переписке, и она открывается тем же видом.
  const query = useDishes()
  const idti = useNavigate()

  return (
    <section>
      <h1>Блюда</h1>
      {/* Таблица — только когда список пришёл: пустой список до ответа
          показал бы «Блюд пока нет». */}
      {estDannye(query) ? (
        <>
          <SboyObnovleniya query={query} />
          <TablitsaSFiltrami
            stroki={query.data}
            kolonki={KOLONKI_BLYUD}
            poiskPo={poiskPo}
            rowKey={klyuch}
            rowClass={klassStroki}
            // Вид списка — в state: «← Блюда» на карточке вернёт к нему же,
            // с сортировкой, фильтрами и поиском, набранным только что.
            onOpen={(r, adresSpiska) => idti(`/dishes/${r.legacy_id}`, { state: adresSpiska })}
            empty="Блюд пока нет"
          />
        </>
      ) : (
        <Sostoyanie query={query} />
      )}
    </section>
  )
}
