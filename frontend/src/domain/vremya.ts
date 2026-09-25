// Время данных по Москве, где кухня, — а не по часовому поясу телефона.
const CHASY = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  hour: '2-digit',
  minute: '2-digit',
})
const DEN = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  day: '2-digit',
  month: '2-digit',
})

/**
 * «14:35», а если не сегодня по Москве — «23.09 14:35».
 *
 * `iso` разбирается как ISO 8601 целиком, а не по шаблону: сервер отдаёт
 * время как его отдаёт база — с долями секунды и без, со смещением «Z» и
 * «+03:00» (docs/FRONTEND.md, GET /api/sync).
 */
export function vremyaDannyh(iso: string, seychas: Date = new Date()): string {
  const moment = new Date(iso)
  const chasy = CHASY.format(moment)
  const den = DEN.format(moment)
  return den === DEN.format(seychas) ? chasy : `${den} ${chasy}`
}
