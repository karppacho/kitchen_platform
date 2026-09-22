import { useLocation } from 'react-router-dom'

import { RAZDELY } from '../shell/razdely'

/** Раздел, которого ещё нет. Пустая страница молчит о том, когда он
 *  появится, — а это единственное, что здесь можно сказать полезного. */
export function Stub() {
  const { pathname } = useLocation()
  const razdel = RAZDELY.find((r) => r.put === pathname)

  return (
    <section className="zaglushka">
      <h1>{razdel?.nazvanie ?? 'Раздел'}</h1>
      <p className="zaglushka-faza">Появится в: {razdel?.faza ?? 'позже'}</p>
      <p>{razdel?.opisanie}</p>
    </section>
  )
}
