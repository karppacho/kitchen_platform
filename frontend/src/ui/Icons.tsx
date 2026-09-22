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
