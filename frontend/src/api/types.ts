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
  /** Степень похожести 0–1. Бэкенд пока отдаёт null. */
  score: number | null
}

export type LinkStatus = 'ambiguous' | 'candidate' | 'orphan'

export type ReconciliationRow = {
  card_id: number
  name: string
  link_status: LinkStatus
  supplier: string
  candidates: Candidate[]
}

export type Reconciliation = {
  total: number
  linked: number
  needs_human: number
  rows: ReconciliationRow[]
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
