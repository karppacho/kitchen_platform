/** Форма слова для числа: 1 замечание, 2 замечания, 5 замечаний; 11–14 — «многие». */
function forma(n: number, odno: string, dva: string, pyat: string): string {
  const sto = n % 100
  if (sto >= 11 && sto <= 14) return pyat
  const desyat = sto % 10
  if (desyat === 1) return odno
  if (desyat >= 2 && desyat <= 4) return dva
  return pyat
}

/** «N замечаний» для подписи строки на телефоне; ноль — ничего: «0 замечаний» шумит. */
export function zamechaniyaSlovami(n: number): string | null {
  if (n <= 0) return null
  return `${n} ${forma(n, 'замечание', 'замечания', 'замечаний')}`
}
