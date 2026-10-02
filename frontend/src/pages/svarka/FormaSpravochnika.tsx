import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import { ApiError, NE_POLUCHILOS } from '../../api/client'
import { POVTOROV_PERENOSA, usePerenos, useStrokaSpravochnika } from '../../api/svarka'
import type {
  ReconciliationRow,
  ReferenceForm,
  ReferenceRowCurrent,
  ReferenceRowPreview,
  ReferenceRowReason,
  ReferenceTransfer,
} from '../../api/types'
import { podskazkaPoteri, summaPoter } from '../../domain/poteri'
import { Num } from '../../ui/Num'
import './formaSpravochnika.css'

type Pole =
  | 'short_name'
  | 'unit'
  | 'price_per_kg'
  | 'price_per_pack'
  | 'weight_per_piece_g'
  | 'losses_unpacking'
  | 'losses_cutting'
  | 'losses_thermal'

/** Что в полях формы — строками, как набрано: число делает сервер. */
type Znacheniya = Record<Pole, string>

/** Единицы, которые принимает справочник: от неё зависит смысл цены за
 *  единицу и нужен ли вес штуки. */
const EDINITSY = ['кг', 'л', 'шт'] as const

/** Названия — те же, что у сервера в текстах ошибок. */
const NAZVANIYA: Readonly<Record<Pole, string>> = {
  short_name: 'Короткое имя для iiko',
  unit: 'Единица измерения',
  price_per_kg: 'Цена за 1 кг (или 1 шт / 1 л), ₽',
  price_per_pack: 'Цена за упаковку, ₽',
  weight_per_piece_g: 'Вес 1 шт, г',
  losses_unpacking: 'Потери при перетарке и дефросте, %',
  losses_cutting: 'Потери при нарезке, %',
  losses_thermal: 'Потери при тепловой обработке, %',
}

const POTERI = ['losses_unpacking', 'losses_cutting', 'losses_thermal'] as const

/** Порядок полей на экране — по нему фокус ведётся к первому полю с ошибкой. */
const PORYADOK: readonly Pole[] = [
  'short_name',
  'unit',
  'price_per_pack',
  'price_per_kg',
  'weight_per_piece_g',
  ...POTERI,
]

/** Короткое имя длиннее сервер не примет — набрать больше не даём. */
const PREDEL_IMENI = 100

/** Причины, при которых строку стоит перечитать: таблица подтянет её сама
 *  или шеф поправит лист. */
const PROVERIT_ESHCHYO: readonly ReferenceRowReason[] = [
  'not_yet',
  'shifted',
  'ambiguous',
  'formula',
  'percent',
  'losses_empty',
]

/** Все попытки оборвались или не дождались ответа. Запись могла и лечь —
 *  повтор это узнает и второй не сделает. */
const NE_DOSHLO =
  'Ответ не дошёл — проверьте связь и нажмите ещё раз: второй записи в справочнике не будет.'

/** Сбой сервера без текста — страница nginx (сервер перезапускается). */
const SERVER_NE_OTVETIL =
  'Сервер не ответил — нажмите ещё раз через минуту: второй записи в справочнике не будет.'

/** 422 без ошибок по полям — запрос испорчен экраном, а не человеком. */
const ISPORCHEN = 'Сервер не принял форму — обновите страницу и попробуйте ещё раз.'

function idPolya(pole: Pole): string {
  return `svarka-pole-${pole}`
}

function idOshibki(pole: Pole): string {
  return `svarka-oshibka-${pole}`
}

/** Число от сервера — в поле: с запятой, как пишут люди; сервер примет и
 *  «12,5», и «12.5». Пустого нет — `pusto`. */
function vPole(chislo: string | null | undefined, pusto = ''): string {
  return chislo === null || chislo === undefined ? pusto : chislo.replace('.', ',')
}

/**
 * Начальные значения — текущие ручные ячейки строки: умолчания заготовки
 * шефа или прежняя правка человека. Единица не из кг, л, шт — не выбрана:
 * выбирает человек. Пустые потери — 0.
 */
function izStroki(tekushchie: ReferenceRowCurrent | null | undefined): Znacheniya {
  const edinitsa = tekushchie?.unit.trim().toLowerCase() ?? ''
  return {
    short_name: tekushchie?.short_name ?? '',
    unit: (EDINITSY as readonly string[]).includes(edinitsa) ? edinitsa : '',
    price_per_kg: vPole(tekushchie?.price_per_kg),
    price_per_pack: vPole(tekushchie?.price_per_pack),
    weight_per_piece_g: vPole(tekushchie?.weight_per_piece_g),
    losses_unpacking: vPole(tekushchie?.losses_unpacking, '0'),
    losses_cutting: vPole(tekushchie?.losses_cutting, '0'),
    losses_thermal: vPole(tekushchie?.losses_thermal, '0'),
  }
}

/** Тело запроса. Цену за единицу не шлём, если в строке она формула: её
 *  считает таблица. Вес — только у «шт»: у кг и л сервер его отвергнет. */
function vTelo(znacheniya: Znacheniya, lFormula: boolean): ReferenceForm {
  return {
    short_name: znacheniya.short_name,
    unit: znacheniya.unit,
    ...(lFormula ? {} : { price_per_kg: znacheniya.price_per_kg }),
    price_per_pack: znacheniya.price_per_pack,
    weight_per_piece_g: znacheniya.unit === 'шт' ? znacheniya.weight_per_piece_g : null,
    losses_unpacking: znacheniya.losses_unpacking,
    losses_cutting: znacheniya.losses_cutting,
    losses_thermal: znacheniya.losses_thermal,
  }
}

/**
 * Что сказать об отказе записи. Текст сервера написан для человека — что
 * случилось и что делать — и показывается как есть. Свой — только когда
 * сервер ничего не сказал: связь оборвалась, ответил nginx, запрос испорчен.
 */
function tekstOtkaza(oshibka: unknown): string {
  if (!(oshibka instanceof ApiError) || oshibka.status === 0) return NE_DOSHLO
  if (oshibka.status === 422 && oshibka.errors === null) return ISPORCHEN
  if (oshibka.status >= 500 && oshibka.message === NE_POLUCHILOS) return SERVER_NE_OTVETIL
  return oshibka.message
}

/** Что сказать об отказе чтения строки. Чтение ничего не пишет — о второй
 *  записи здесь говорить незачем. */
function tekstOtkazaChteniya(oshibka: unknown): string {
  if (!(oshibka instanceof ApiError) || oshibka.status === 0) {
    return 'Нет связи с сервером — проверьте связь и нажмите «Проверить ещё раз».'
  }
  if (oshibka.status >= 500 && oshibka.message === NE_POLUCHILOS) {
    return 'Сервер не ответил — нажмите «Проверить ещё раз» через минуту.'
  }
  return oshibka.message
}

function tekstKhoda(popytka: number, uzhe: boolean): string {
  if (popytka > 1) {
    return (
      `Связь прервалась — ${uzhe ? 'связываем' : 'записываем'} ещё раз ` +
      `(попытка ${popytka} из ${POVTOROV_PERENOSA + 1}) — второй записи не будет.`
    )
  }
  return uzhe
    ? 'Связываем карточку с ингредиентом — это может занять до двух минут.'
    : 'Записываем в справочник — это может занять до двух минут.'
}

/**
 * Форма «Добавить в справочник»: ручные ячейки строки ING, которую формула
 * таблицы уже создала для карточки.
 *
 * Панелью справа на компьютере и листом снизу на телефоне — своей
 * разметкой. Пока она открыта, список за ней недоступен: запись одна за раз.
 *
 * Подтянутое формулой (категория, название, изготовитель, КБЖУ) — только
 * показать. id выдаёт сервер при записи — человек его видит, но не вводит.
 * Поля начинаются с текущих значений строки; набранное человеком они не
 * перебивают, даже когда строку перечитали.
 *
 * Отказ сервера — его текстом; ошибка поля — у поля. Форма с набранным
 * остаётся при любом отказе: закрывает её только успех или сам человек.
 */
export function FormaSpravochnika({
  stroka,
  onZakryt,
  onZapisano,
}: {
  stroka: ReconciliationRow
  onZakryt: () => void
  onZapisano: (otvet: ReferenceTransfer) => void
}) {
  const predprosmotr = useStrokaSpravochnika(stroka.card_id)
  const perenos = usePerenos(stroka.card_id, onZapisano)
  // Только то, что человек поменял сам: прочее идёт из строки и обновляется
  // вместе с ней.
  const [pravki, zadatPravki] = useState<Partial<Znacheniya>>({})
  const zagolovok = useRef<HTMLHeadingElement>(null)

  // Кнопка, на которой был фокус, осталась под панелью — фокус в форму.
  useEffect(() => zagolovok.current?.focus(), [])

  const dannye = predprosmotr.data
  const lFormula = dannye?.formulas.includes('L') ?? false
  const znacheniya: Znacheniya = { ...izStroki(dannye?.current), ...pravki }
  const izmenit = (pole: Pole, znachenie: string) =>
    zadatPravki((prezhnie) => ({ ...prezhnie, [pole]: znachenie }))

  const otkaz = perenos.oshibka
  const oshibkiPolej = otkaz instanceof ApiError && otkaz.status === 422 ? otkaz.errors : null
  const neSoglasovana =
    dannye?.reason === 'not_approved' ||
    (otkaz instanceof ApiError && otkaz.reason === 'not_approved')
  const uzhe = dannye?.reason === 'already'

  // «Уже в справочнике» — без формы: пару подтверждают значениями строки. Но
  // если сервер их не принял (пусто короткое имя, не та единица), поля
  // нужны — человек вводит их сам, и они остаются до конца.
  const [polyaDlyaUzhe, zadatPolyaDlyaUzhe] = useState(false)
  if (uzhe && oshibkiPolej !== null && !polyaDlyaUzhe) zadatPolyaDlyaUzhe(true)
  const pokazatPolya = dannye !== undefined && !neSoglasovana && (!uzhe || polyaDlyaUzhe)

  const vidimye: readonly Pole[] = PORYADOK.filter(
    (pole) =>
      pokazatPolya &&
      !(pole === 'price_per_kg' && lFormula) &&
      !(pole === 'weight_per_piece_g' && znacheniya.unit !== 'шт'),
  )
  const uPolya = (pole: Pole): string | null =>
    vidimye.includes(pole) ? (oshibkiPolej?.[pole] ?? null) : null
  // Ошибки полей, которых на экране нет, — общим текстом, а не молча.
  const bezPolya = Object.entries(oshibkiPolej ?? {})
    .filter(([pole]) => !vidimye.includes(pole as Pole))
    .map(([, tekst]) => tekst)
  const obshchiyOtkaz =
    otkaz === null
      ? null
      : oshibkiPolej === null
        ? tekstOtkaza(otkaz)
        : bezPolya.join('; ') || null

  // Сервер отказал по полям — фокус к первому из видимых: на телефоне оно
  // бывает выше края экрана.
  useEffect(() => {
    if (!(otkaz instanceof ApiError) || otkaz.errors === null) return
    for (const pole of PORYADOK) {
      if (otkaz.errors[pole] === undefined) continue
      const element = document.getElementById(idPolya(pole))
      if (element) {
        element.focus()
        return
      }
    }
  }, [otkaz])

  const chitaem = predprosmotr.isFetching
  const zanyato = perenos.idyot || chitaem
  // Строка ждёт переноса — или её ещё нет: таблица могла подтянуть её с тех
  // пор, а проверит сервер свежим чтением. Сдвиг, тёзки, не те ячейки —
  // нужен человек в листе, отправлять нечего.
  const pokazatZapisat = dannye !== undefined && !neSoglasovana
  const mozhnoZapisat = pokazatZapisat && (dannye.ready || dannye.reason === 'not_yet' || uzhe)
  const proverit =
    !uzhe &&
    !neSoglasovana &&
    (predprosmotr.isError || (dannye?.reason != null && PROVERIT_ESHCHYO.includes(dannye.reason)))

  function otpravit(sobytie: FormEvent) {
    sobytie.preventDefault()
    if (!mozhnoZapisat || zanyato) return
    perenos.zapisat(vTelo(znacheniya, lFormula))
  }

  function perechitat() {
    perenos.sbrosit()
    void predprosmotr.refetch()
  }

  function klavisha(sobytie: KeyboardEvent) {
    if (sobytie.key === 'Escape' && !perenos.idyot) onZakryt()
  }

  return (
    <>
      <div className="svarka-fon" aria-hidden="true" />
      <div
        className="svarka-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="svarka-zagolovok"
        onKeyDown={klavisha}
      >
        <div className="svarka-panel-shapka">
          <div>
            <h2 id="svarka-zagolovok" ref={zagolovok} tabIndex={-1}>
              Добавить в справочник
            </h2>
            <p className="svarka-imya">{stroka.name}</p>
          </div>
          <button type="button" onClick={onZakryt} disabled={perenos.idyot}>
            Закрыть
          </button>
        </div>

        <form className="svarka-panel-telo" onSubmit={otpravit} noValidate>
          <div role="status" className="svarka-zhivaya">
            {chitaem && <p className="svarka-poyasnenie">Читаем строку справочника…</p>}
            {perenos.idyot && <p className="svarka-zhdyom">{tekstKhoda(perenos.popytka, uzhe)}</p>}
          </div>

          {predprosmotr.isError && (
            <p className="svarka-otkaz" role="alert">
              {tekstOtkazaChteniya(predprosmotr.error)}
            </p>
          )}

          {dannye && (otkaz === null || uzhe) && dannye.message !== null && (
            <p className={uzhe ? 'svarka-poyasnenie' : 'svarka-zamechanie'}>{dannye.message}</p>
          )}

          {dannye && !neSoglasovana && <Podtyanuto dannye={dannye} uzhe={uzhe} />}

          {pokazatPolya && (
            <Polya znacheniya={znacheniya} izmenit={izmenit} uPolya={uPolya} lFormula={lFormula} />
          )}

          {obshchiyOtkaz !== null && (
            <p className="svarka-otkaz" role="alert">
              {obshchiyOtkaz}
            </p>
          )}

          {(pokazatZapisat || proverit) && (
            <div className="svarka-panel-knopki">
              {proverit && (
                <button type="button" onClick={perechitat} disabled={zanyato}>
                  Проверить ещё раз
                </button>
              )}
              {pokazatZapisat && (
                <button
                  type="submit"
                  className="svarka-glavnaya"
                  disabled={!mozhnoZapisat || zanyato}
                >
                  {uzhe ? 'Связать с карточкой' : 'Записать в справочник'}
                </button>
              )}
            </div>
          )}
        </form>
      </div>
    </>
  )
}

/** Что формула таблицы уже вывела в строку, и какой будет id, — текстом:
 *  менять это в форме нельзя. */
function Podtyanuto({ dannye, uzhe }: { dannye: ReferenceRowPreview; uzhe: boolean }) {
  const { pulled } = dannye
  return (
    <dl className="svarka-podtyanuto">
      {dannye.row !== null && (
        <>
          <dt>Строка листа ING</dt>
          <dd>{dannye.row}</dd>
        </>
      )}
      {!uzhe && (
        <>
          <dt>id</dt>
          <dd>{`будет ${dannye.next_id} — выдаётся при записи`}</dd>
        </>
      )}
      {pulled && (
        <>
          <dt>Категория</dt>
          <dd>{pulled.category}</dd>
          <dt>Название</dt>
          <dd>{pulled.name}</dd>
          <dt>Полное название</dt>
          <dd>{pulled.full_name}</dd>
          <dt>Изготовитель</dt>
          <dd>{pulled.manufacturer}</dd>
          <dt>Состав</dt>
          <dd>{pulled.composition}</dd>
          <dt>КБЖУ на 100 г</dt>
          <dd className="svarka-kbzhu">
            <span>
              Б <Num value={pulled.protein} />
            </span>
            <span>
              Ж <Num value={pulled.fat} />
            </span>
            <span>
              У <Num value={pulled.carbs} />
            </span>
            <span>
              <Num value={pulled.kcal} unit="ккал" />
            </span>
          </dd>
        </>
      )}
    </dl>
  )
}

/**
 * Поля формы. Пока идёт запись, они не блокируются: ушло то, что было при
 * нажатии, а отключённое поле отняло бы фокус. Отказ — и форма снова уходит
 * с тем, что в полях сейчас.
 */
function Polya({
  znacheniya,
  izmenit,
  uPolya,
  lFormula,
}: {
  znacheniya: Znacheniya
  izmenit: (pole: Pole, znachenie: string) => void
  uPolya: (pole: Pole) => string | null
  lFormula: boolean
}) {
  const oshibkaEdinitsy = uPolya('unit')
  const summa = summaPoter(POTERI.map((pole) => znacheniya[pole]))
  const vvod = (pole: Pole, chislo: boolean) => (
    <Vvod
      pole={pole}
      znachenie={znacheniya[pole]}
      izmenit={izmenit}
      oshibka={uPolya(pole)}
      chislo={chislo}
    />
  )

  return (
    <>
      {vvod('short_name', false)}

      <div className="svarka-pole">
        {/* Три варианта — кнопками-переключателями во всю ширину: в них
            попадают пальцем. */}
        <fieldset
          className="svarka-edinitsy"
          aria-describedby={oshibkaEdinitsy !== null ? idOshibki('unit') : undefined}
        >
          <legend>{NAZVANIYA.unit}</legend>
          {EDINITSY.map((edinitsa, nomer) => (
            <label key={edinitsa}>
              <input
                type="radio"
                name="svarka-edinitsa"
                id={nomer === 0 ? idPolya('unit') : undefined}
                value={edinitsa}
                checked={znacheniya.unit === edinitsa}
                onChange={() => izmenit('unit', edinitsa)}
              />
              {edinitsa}
            </label>
          ))}
        </fieldset>
        <OshibkaPolya pole="unit" tekst={oshibkaEdinitsy} />
      </div>

      {vvod('price_per_pack', true)}

      {lFormula ? (
        <p className="svarka-poyasnenie">Цена за 1 кг (или 1 шт / 1 л) — считается в таблице</p>
      ) : (
        vvod('price_per_kg', true)
      )}

      {znacheniya.unit === 'шт' && vvod('weight_per_piece_g', true)}

      <fieldset className="svarka-gruppa">
        <legend>Потери</legend>
        {POTERI.map((pole) => (
          <Vvod
            key={pole}
            pole={pole}
            znachenie={znacheniya[pole]}
            izmenit={izmenit}
            oshibka={uPolya(pole)}
            podskazka={podskazkaPoteri(znacheniya[pole])}
            chislo
          />
        ))}
        {/* Итог — как формула P листа: сумма трёх, для сверки с таблицей. */}
        <p className="svarka-itog-poter">
          Общие потери: <Num value={summa} unit="%" /> — для сверки с таблицей
        </p>
      </fieldset>
    </>
  )
}

function Vvod({
  pole,
  znachenie,
  izmenit,
  oshibka,
  podskazka = null,
  chislo,
}: {
  pole: Pole
  znachenie: string
  izmenit: (pole: Pole, znachenie: string) => void
  oshibka: string | null
  /** Подсказка формы — не отказ: решает сервер. */
  podskazka?: string | null
  chislo: boolean
}) {
  const idPodskazki = `svarka-podskazka-${pole}`
  const opisanie = [
    podskazka !== null ? idPodskazki : null,
    oshibka !== null ? idOshibki(pole) : null,
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <div className="svarka-pole">
      <label htmlFor={idPolya(pole)}>{NAZVANIYA[pole]}</label>
      <input
        id={idPolya(pole)}
        className="svarka-vvod"
        type="text"
        inputMode={chislo ? 'decimal' : undefined}
        maxLength={pole === 'short_name' ? PREDEL_IMENI : undefined}
        autoComplete="off"
        value={znachenie}
        aria-invalid={oshibka !== null ? true : undefined}
        aria-describedby={opisanie === '' ? undefined : opisanie}
        onChange={(sobytie) => izmenit(pole, sobytie.target.value)}
      />
      {podskazka !== null && (
        <p id={idPodskazki} className="svarka-podskazka">
          {podskazka}
        </p>
      )}
      <OshibkaPolya pole={pole} tekst={oshibka} />
    </div>
  )
}

function OshibkaPolya({ pole, tekst }: { pole: Pole; tekst: string | null }) {
  if (tekst === null) return null
  return (
    <p id={idOshibki(pole)} className="svarka-oshibka-polya">
      {tekst}
    </p>
  )
}
