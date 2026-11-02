import type { Razdel } from './razdely'

/**
 * Раздел, которому принадлежит адрес: `/dishes/B001` — карточка блюда, и в
 * шапке телефона стоит «Блюда». Префикс считается только до `/`, чтобы
 * `/dishesX` не оказался «Блюдами». Ничего не нашлось — null: шапка
 * покажет «Кухня».
 */
export function razdelPoAdresu(pathname: string, razdely: readonly Razdel[]): Razdel | null {
  return razdely.find((r) => pathname === r.put || pathname.startsWith(`${r.put}/`)) ?? null
}
