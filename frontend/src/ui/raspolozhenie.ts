/**
 * Где встать панели фильтра под значком колонки.
 *
 * Панель — в портале в `document.body`, поэтому координаты — документа:
 * внутри `th` её обрезала бы прокрутка обёртки таблицы. Значок меряется в
 * координатах окна (`getBoundingClientRect`), прокрутка прибавляется
 * после прижима — прижимаем к краям видимого окна, а не документа.
 */

/** Ширина панели, px: её ставит панели Vsplyvashka, по ней считается прижим. */
export const SHIRINA_PANELI = 264

// От края окна — два шага сетки (--shag 4px), от низа значка — один.
const OTSTUP_OT_KRAYA = 8
const ZAZOR_POD_ZNACHKOM = 4

export type Yakor = { left: number; right: number; bottom: number }
export type Prokrutka = { x: number; y: number }

/**
 * `vyravnivanie` — как выровнена колонка: у колонки влево панель
 * начинается под левым краем значка, у колонки вправо (числа) —
 * кончается под правым, чтобы лечь над своей колонкой, а не за таблицей.
 * `shirinaOkna` — без полосы прокрутки (`clientWidth`): иначе прижатая
 * панель заходила бы под полосу и давала горизонтальную прокрутку.
 */
export function raspolozhit(
  yakor: Yakor,
  vyravnivanie: 'left' | 'right',
  shirinaOkna: number,
  prokrutka: Prokrutka,
): { left: number; top: number } {
  const zhelaemoe = vyravnivanie === 'right' ? yakor.right - SHIRINA_PANELI : yakor.left
  const pravyiPredel = shirinaOkna - SHIRINA_PANELI - OTSTUP_OT_KRAYA
  // Левый предел — последним: если окно уже панели, пусть лучше вылезет
  // правый край, чем начало списка.
  const left = Math.max(OTSTUP_OT_KRAYA, Math.min(zhelaemoe, pravyiPredel))
  return { left: left + prokrutka.x, top: yakor.bottom + ZAZOR_POD_ZNACHKOM + prokrutka.y }
}
