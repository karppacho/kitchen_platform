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
