/** Контракты ручек. Числа приходят строками, чтобы не потерять копейки
 *  на округлении float, и строками остаются: арифметики над ними здесь
 *  нет и быть не должно. */

export type Me = {
  email: string
  display_name: string
  roles: string[]
}

export type Ingredient = {
  id: number
  /** Идентификатор из Google-таблицы. Шеф знает в лицо именно его. */
  legacy_id: string
  name: string
  category: string
  /** «кг», «шт», «л», «мл». Определяет смысл price_per_kg. */
  unit: string
  /** Строка из таблицы, а не перечисление: «активный» у ингредиентов,
   *  «активное» у блюд. Значения фильтра берутся из ответа. */
  status: string
  /** При unit «шт» это цена за штуку, несмотря на имя поля. */
  price_per_kg: string | null
  /** Только у штучных. У весовых null — это норма, а не пропуск. */
  weight_per_piece_g: string | null
  has_card: boolean
}

export type Dish = {
  legacy_id: string
  name: string
  category: string
  status: string
  price_menu: string | null
  uc_rub: string
  uc_percent: string | null
  margin_percent: string | null
  output_grams: string
  warnings: number
}

export type Component = {
  name: string
  /** «Короткое для айки». Бывает пустым — тогда показывается name. */
  short_name: string
  row_type: 'main' | 'packaging'
  unit: string
  net_weight_g: string
  /** У упаковки брутто нет: в выход блюда она не входит. */
  gross_weight_g: string | null
  price_per_unit: string | null
  cost_rub: string
  share_percent: string | null
}

export type DishDetail = Dish & {
  protein_g: string
  fat_g: string
  carbs_g: string
  kcal: string
  /** Доля веса состава с заполненным КБЖУ, 0–1. Ниже 0.5 цифрам верить нельзя. */
  kbju_coverage: string
  components: Component[]
  warning_texts: string[]
}

export type Candidate = {
  ingredient_id: number
  legacy_id: string
  name: string
  /** Похожесть названий 0–1 — оценка для порядка, а не величина, поэтому
   *  числом. У похожих (`candidate`) есть, у тёзок (`ambiguous`) — null:
   *  название совпало точно. */
  score: number | null
}

export type LinkStatus = 'ambiguous' | 'candidate' | 'orphan'

/**
 * Что можно сделать с карточкой на «Сверке» — решает сервер:
 * - `confirm` — «Это он»: выбрать ингредиент из `candidates`;
 * - `to_reference` — форма переноса в справочник: «Добавить в справочник»
 *   у карточки без пары, «Это новый» у спорной. Только у согласованной.
 */
export type ReconciliationAction = 'confirm' | 'to_reference'

export type ReconciliationRow = {
  card_id: number
  name: string
  link_status: LinkStatus
  supplier: string
  /** «Да» в книге карточек — по последнему переносу в базу. */
  approved: boolean
  /** У `ambiguous` — тёзки, у `candidate` — похожие, у `orphan` — пусто. */
  candidates: Candidate[]
  /** Пусто — только пояснение, без кнопки. */
  actions: ReconciliationAction[]
}

export type Reconciliation = {
  total: number
  linked: number
  needs_human: number
  rows: ReconciliationRow[]
}

/** «Это он» подтверждён: ответ POST /api/reconciliation/{id}/confirm. */
export type ConfirmedPair = {
  card_id: number
  ingredient_id: number
  legacy_id: string
  name: string
  /** Пара уже была подтверждена этим ингредиентом — повторное нажатие. */
  already: boolean
  /** «Пара подтверждена: ингредиент «…», id N». */
  message: string
}

/**
 * Почему строку ING нельзя заполнить сейчас:
 * - `not_approved` — карточка не «Да»;
 * - `not_yet` — строки ещё нет: таблица подтягивает карточки с задержкой;
 * - `shifted`, `ambiguous` — строки съехали или тёзок несколько: нужен
 *   человек в листе;
 * - `formula`, `percent`, `losses_empty` — ячейки строки не те;
 * - `confirmed` — пару уже подтвердил человек («Это он» или перенос у
 *   другого): список на экране устарел, писать нечего;
 * - `already` — id в строке уже стоит.
 */
export type ReferenceRowReason =
  | 'not_approved'
  | 'not_yet'
  | 'shifted'
  | 'ambiguous'
  | 'formula'
  | 'percent'
  | 'losses_empty'
  | 'confirmed'
  | 'already'

/** Что формула QUERY уже вывела в строку ING — только показать. */
export type ReferenceRowPulled = {
  category: string
  name: string
  full_name: string
  manufacturer: string
  composition: string
  protein: string | null
  fat: string | null
  carbs: string | null
  kcal: string | null
}

/**
 * Ручные ячейки строки ING сейчас — в тех же ключах и единицах, что форма:
 * потери в процентах («12.5» — 12,5 %). Пустая текстовая — «», пустая
 * числовая — null.
 */
export type ReferenceRowCurrent = {
  short_name: string
  unit: string
  status: string
  /** L. Если L — формула, то, что она показывает. */
  price_per_kg: string | null
  price_per_pack: string | null
  weight_per_piece_g: string | null
  losses_unpacking: string | null
  losses_cutting: string | null
  losses_thermal: string | null
}

/** Строка ING карточки для формы переноса — свежее чтение листа, без записи.
 *  Ответ GET /api/reconciliation/{id}/reference-row. */
export type ReferenceRowPreview = {
  /** id, который получила бы строка сейчас, — справочно: при записи его
   *  выдают заново. */
  next_id: string
  row: number | null
  /** Форму можно отправлять. */
  ready: boolean
  reason: ReferenceRowReason | null
  /** То же словами — показать как есть. */
  message: string | null
  /** id из колонки A — при `already`. */
  ref_id: string | null
  /** Ручные ячейки с формулой, буквами; P — всегда. L здесь — цену за
   *  единицу считает таблица. */
  formulas: string[]
  pulled: ReferenceRowPulled | null
  current: ReferenceRowCurrent | null
}

/**
 * Форма переноса — поля строками, как ввёл человек; null — пусто. Потери —
 * в процентах. Статуса и id нет: статус всегда «активный», id выдаёт сервер.
 */
export type ReferenceForm = {
  short_name: string | null
  unit: string | null
  /** L. Не слать, если L в строке — формула. */
  price_per_kg?: string | null
  price_per_pack: string | null
  /** Только для «шт». */
  weight_per_piece_g: string | null
  losses_unpacking: string | null
  losses_cutting: string | null
  losses_thermal: string | null
}

/** Карточка в справочнике: ответ POST /api/reconciliation/{id}/to-reference.
 *  Повтор (ответ потерялся) отвечает той же строкой: второй записи нет. */
export type ReferenceTransfer = {
  row: number
  ref_id: string
  /** Ингредиент был в справочнике и до этого нажатия. */
  already: boolean
  /** Наш ингредиент виден на сайте; false — оговорка в `notes`. */
  imported: boolean
  /** Пара подтверждена — карточка ушла со «Сверки». */
  linked: boolean
  ingredient_id: number | null
  /** «Записано в справочник: строка N, id X». */
  message: string
  /** Прежняя попытка, раскладку которой не подтвердили, — для шефа. */
  shifted: { row: number; journal_id: number } | null
  /** Оговорки готовыми фразами — показать как есть. */
  notes: string[]
}

/** Книга — Google-таблица. Ответ GET /api/sync. */
export type SyncBook = {
  book: string
  /** Со строчной: стоит посреди фразы. */
  title: string
  checked_at: string | null
  changed_at: string | null
  /** Старше 15 минут — считает сервер по своим часам. */
  stale: boolean
  /** Причина сбоя словами для всех, не код ошибки. */
  problem: string | null
  problem_since: string | null
}

export type SyncStatus = {
  data_as_of: string | null
  changed_at: string | null
  stale: boolean
  books: SyncBook[]
}

/** Шаг мастера «Новый ингредиент», как его хранит черновик на сервере.
 *  Порядок — `SHAGI` в api/kartochki.ts («Шаг N из 9»). */
export type Shag =
  | 'supplier'
  | 'category'
  | 'name'
  | 'label'
  | 'review'
  | 'approval'
  | 'photos'
  | 'description'
  | 'summary'

export type VidFoto = 'label' | 'package' | 'before' | 'after'

/** Согласован ли продукт — ровно то, что ляжет в колонку листа. */
export type Soglasovanie = 'Да' | 'Отбракован'

/**
 * Незаконченная карточка повара — черновик на сервере, один активный на
 * повара. Ответ GET /api/cards/drafts/current и всех правок.
 */
export type Draft = {
  id: string
  status: string
  step: Shag
  supplier: string
  category: string
  name: string
  label_name: string
  manufacturer: string
  composition: string
  /** КБЖУ на 100 г — строками, без хвостовых нулей («12.5», «100»). */
  protein: string | null
  fat: string | null
  carbs: string | null
  kcal: string | null
  shelf_life_sealed: string
  shelf_life_defrost: string
  shelf_life_after: string
  defrost_conditions: string
  description: string
  approval: Soglasovanie | null
  /** Какие фото есть. Сами фото — только через прокси сервера. */
  photos: Record<VidFoto, boolean>
  /** `running` — распознавание идёт, черновик надо опрашивать. */
  recognition_status: 'running' | 'done' | 'failed' | null
  recognition_error: string | null
  warnings: string[]
  /** Чего не хватает для отправки — словами для повара. */
  missing: string[]
  created_at: string
  updated_at: string
}

/** Правка черновика: меняются только переданные поля. `null` в тексте —
 *  пусто, в КБЖУ и согласовании — «нет». */
export type DraftPatch = {
  step?: Shag
  supplier?: string | null
  category?: string | null
  name?: string | null
  label_name?: string | null
  manufacturer?: string | null
  composition?: string | null
  protein?: string | null
  fat?: string | null
  carbs?: string | null
  kcal?: string | null
  shelf_life_sealed?: string | null
  shelf_life_defrost?: string | null
  shelf_life_after?: string | null
  defrost_conditions?: string | null
  description?: string | null
  approval?: Soglasovanie | null
}

/**
 * Ответ «Отправить в таблицу» — карточка в листе. Повтор отправки (ответ
 * потерялся в сети) отвечает той же строкой: второй строки нет.
 */
export type Submitted = {
  /** Строка листа: «Записано в таблицу, строка N». */
  row: number
  /** Карточка легла раньше — повтором отправки или прерванной попыткой. */
  already_written: boolean
  /** Карточка уже на сайте; `false` — появится при следующем обновлении. */
  imported: boolean
  /** Название, как записано в лист. */
  name: string
  /** Правки, не попавшие в лист, — названиями колонок листа. */
  not_written: string[]
  /** Прежняя попытка, при которой таблицу меняли: строку и запись журнала
   *  повар показывает шефу. */
  shifted: { row: number; journal_id: number } | null
  /** Оговорки готовыми фразами — показать повару как есть. */
  notes: string[]
}

/** Варианты выбора — частые первыми. «Другая…» сервер не отдаёт: это
 *  действие экрана, а не категория. */
export type CardOptions = {
  categories: string[]
  suppliers: string[]
}

export type CardHit = {
  name: string
  supplier: string
}

/**
 * Есть ли уже такое название. Похожие ищутся, только когда точных нет.
 *
 * - `cards.exact` — дубль в листе: такую строку не запишут;
 * - `hidden` — карточки, убранные из листа: имя свободно, но новая строка
 *   вернёт на сайт старую карточку со старыми связями;
 * - `reference` — имена справочника, только имена: взяв его, карточка
 *   склеится с позицией сама.
 */
export type NameCheck = {
  cards: { exact: CardHit[]; similar: CardHit[] }
  hidden: { exact: CardHit[]; similar: CardHit[] }
  reference: { exact: string[]; similar: string[] }
}
