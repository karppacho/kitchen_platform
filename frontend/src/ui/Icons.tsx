import type { ReactNode } from 'react'

import type { Napravlenie } from '../domain/tablitsa'

/**
 * Рисованные SVG-иконки в одном стиле: контур, currentColor, толщина 2.
 * Эмодзи в интерфейсе запрещены — это единственный источник иконок.
 */
type SvoystvaIkonki = {
  className?: string
}

/** Гамбургер — открывает меню разделов на узком экране. */
export function MenuIcon({ className }: SvoystvaIkonki) {
  return (
    <svg
      className={className}
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <line x1="3" y1="5" x2="17" y2="5" />
      <line x1="3" y1="10" x2="17" y2="10" />
      <line x1="3" y1="15" x2="17" y2="15" />
    </svg>
  )
}

/**
 * Малые иконки таблицы — в шапке колонки и на фишках фильтров. Та же сетка
 * 20×20 и толщина 2, что у меню, но 14 px: строка шапки не должна стать
 * выше, иначе на 1920×1080 влезет меньше 25–30 строк.
 */
function MalayaIkonka({ className, children }: SvoystvaIkonki & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width="14"
      height="14"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/**
 * Сортировка колонки: без направления — две стрелки (можно отсортировать),
 * по возрастанию — стрелка вверх, по убыванию — вниз. Для программы чтения
 * с экрана порядок сообщает `aria-sort` заголовка, иконка — только глазу.
 */
export function SortirovkaIcon({ className, napravlenie }: SvoystvaIkonki & { napravlenie?: Napravlenie | null }) {
  return (
    <MalayaIkonka className={className}>
      {napravlenie === 'vozr' && <path d="M10 16V4M5 9l5-5 5 5" />}
      {napravlenie === 'ubyv' && <path d="M10 4v12M5 11l5 5 5-5" />}
      {!napravlenie && <path d="M6 8l4-4 4 4M6 12l4 4 4-4" />}
    </MalayaIkonka>
  )
}

/** Воронка — фильтр колонки. Активный фильтр заливают из CSS (`fill`). */
export function VoronkaIcon({ className }: SvoystvaIkonki) {
  return (
    <MalayaIkonka className={className}>
      <path d="M3 4h14l-5.5 6.5V16l-3-1.5v-4z" />
    </MalayaIkonka>
  )
}

/** Крестик — снять фильтр (фишка над таблицей). */
export function KrestikIcon({ className }: SvoystvaIkonki) {
  return (
    <MalayaIkonka className={className}>
      <path d="M5 5l10 10M15 5L5 15" />
    </MalayaIkonka>
  )
}
