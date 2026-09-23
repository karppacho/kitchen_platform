import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useDish } from '../api/queries'
import type { Component } from '../api/types'
import { dorozhe } from '../domain/tseny'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
// Ruling 3: классы `.tablitsa` из table.css нужны здесь напрямую — этот
// экран не гарантированно рисуется рядом с <DataTable>, который сам
// импортирует table.css. Без явного импорта состав блюда выглядел бы
// правильно только случайно.
import '../ui/table.css'
import './pages.css'

const PORT_KBJU = 0.5

export function DishDetailPage() {
  const { legacyId = '' } = useParams()
  const query = useDish(legacyId)

  // 404 — не то же самое, что «не удалось получить данные»: это не сбой
  // связи или сервера, а прямой ответ «такой карточки не существует».
  // Кнопка «Повторить» здесь не нужна: тот же legacy_id вернёт тот же 404.
  if (query.isError && query.error instanceof ApiError && query.error.status === 404) {
    return (
      <section className="kartochka">
        <Link to="/dishes" className="nazad">
          ← Блюда
        </Link>
        <div className="sboy" role="alert">
          <h1>Такого блюда нет</h1>
          <p className="sboy-prichina">
            В справочнике нет блюда с идентификатором «{legacyId}».
          </p>
        </div>
      </section>
    )
  }

  if (!query.isSuccess) return <Sostoyanie query={query} />
  const blyudo = query.data

  const osnova = blyudo.components.filter((k) => k.row_type === 'main')
  const upakovka = blyudo.components.filter((k) => k.row_type === 'packaging')
  const pokrytie = Number(blyudo.kbju_coverage)
  const cenaVyshe = dorozhe(blyudo.uc_rub, blyudo.price_menu)

  return (
    <section className="kartochka">
      <Link to="/dishes" className="nazad">
        ← Блюда
      </Link>

      <header className="kartochka-shapka">
        <h1>{blyudo.name}</h1>
        <p className="kartochka-podpis">
          {blyudo.legacy_id} · {blyudo.category} · {blyudo.status}
        </p>
      </header>

      <div className="krupno">
        <Pokazatel podpis="Цена меню">
          <Num value={blyudo.price_menu} fraction={2} unit="₽" />
        </Pokazatel>
        <Pokazatel podpis="Себестоимость">
          {cenaVyshe ? (
            // Почти всегда это перепутанная единица измерения, а не
            // настоящий убыток — подпись направляет к причине, слова
            // «убыток» здесь нет.
            <span
              className="pokazatel-znachenie--ubytok"
              title="Себестоимость выше цены меню — проверьте единицы измерения"
            >
              <Num value={blyudo.uc_rub} fraction={2} unit="₽" />
            </span>
          ) : (
            <Num value={blyudo.uc_rub} fraction={2} unit="₽" />
          )}
        </Pokazatel>
        <Pokazatel podpis="Маржа">
          <Num value={blyudo.margin_percent} fraction={1} unit="%" />
        </Pokazatel>
        <Pokazatel podpis="Выход">
          <Num value={blyudo.output_grams} unit="г" />
        </Pokazatel>
      </div>

      <div className="kbju">
        <span>
          Б <Num value={blyudo.protein_g} fraction={1} unit="г" />
        </span>
        <span>
          Ж <Num value={blyudo.fat_g} fraction={1} unit="г" />
        </span>
        <span>
          У <Num value={blyudo.carbs_g} fraction={1} unit="г" />
        </span>
        <span>
          <Num value={blyudo.kcal} unit="ккал" />
        </span>
        <span className={pokrytie < PORT_KBJU ? 'kbju-slabo' : 'kbju-dolya'}>
          КБЖУ заполнено у <Num value={String(pokrytie * 100)} fraction={1} unit="%" /> веса
          {pokrytie < PORT_KBJU && ' — цифрам доверять нельзя'}
        </span>
      </div>

      <h2>Состав</h2>
      <Sostav osnova={osnova} upakovka={upakovka} />

      {blyudo.warning_texts.length > 0 && (
        <>
          <h2>Замечания</h2>
          {/* Список целиком, а не числом: замечания и есть объяснение
              того, почему себестоимость такая. */}
          <ul className="zamechaniya-spisok">
            {blyudo.warning_texts.map((tekst) => (
              <li key={tekst}>{tekst}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function Pokazatel({ podpis, children }: { podpis: string; children: ReactNode }) {
  return (
    <div className="pokazatel">
      <span className="pokazatel-podpis">{podpis}</span>
      <span className="pokazatel-znachenie">{children}</span>
    </div>
  )
}

// Первая колонка — имя, остальные — числа, прижатые вправо.
const KOLONKI = ['Ингредиент', 'Нетто', 'Брутто', 'Цена за единицу', 'Стоимость', 'Доля']

function Sostav({ osnova, upakovka }: { osnova: Component[]; upakovka: Component[] }) {
  return (
    <div className="tablitsa-obolochka">
      <table className="tablitsa">
        <thead>
          <tr>
            {KOLONKI.map((nazvanie, nomer) => (
              <th key={nazvanie} className={nomer === 0 ? undefined : 'vpravo'}>
                {nazvanie}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {osnova.map((k) => (
            <StrokaSostava key={`${k.name}-${k.net_weight_g}`} k={k} />
          ))}
        </tbody>
        {upakovka.length > 0 && (
          // Упаковка — группой строк в той же таблице, как в макете, а не
          // второй таблицей: у второй ширина колонок считалась бы заново, и
          // её цифры разъезжались бы с цифрами состава. В выход блюда
          // упаковка не входит, брутто у неё нет (gross_weight_g === null).
          <tbody>
            <tr className="stroka-gruppy">
              <th colSpan={KOLONKI.length} scope="colgroup">
                <span role="heading" aria-level={3}>
                  Упаковка
                </span>{' '}
                · в выход блюда не входит, брутто нет
              </th>
            </tr>
            {upakovka.map((k) => (
              <StrokaSostava key={`${k.name}-${k.net_weight_g}`} k={k} />
            ))}
          </tbody>
        )}
      </table>
    </div>
  )
}

function StrokaSostava({ k }: { k: Component }) {
  // Упаковка считается «цена за штуку × количество» (domain/costs.py,
  // add_packaging): в net_weight_g у неё штуки, а не граммы.
  const edinitsa = k.row_type === 'packaging' ? 'шт' : 'г'
  return (
    <tr>
      <td className="imya-ingredienta">
        {/* «Короткое для айки»: технологи работают в iiko и по обычному
            имени не всегда понимают, какой полуфабрикат брать. Бывает
            пустым — тогда показывается name. */}
        {k.short_name || k.name}
        {k.short_name && <span className="polnoe-imya">{k.name}</span>}
      </td>
      <td className="vpravo">
        <Num value={k.net_weight_g} unit={edinitsa} />
      </td>
      <td className="vpravo">
        <Num value={k.gross_weight_g} unit="г" />
      </td>
      <td className="vpravo">
        <Num value={k.price_per_unit} fraction={2} unit={`₽/${k.unit}`} />
      </td>
      <td className="vpravo">
        <Num value={k.cost_rub} fraction={2} unit="₽" />
      </td>
      <td className="vpravo">
        <Num value={k.share_percent} fraction={1} unit="%" />
      </td>
    </tr>
  )
}
