import { useEffect, useState } from 'react'

import { useProverkaImeni } from '../../api/kartochki'
import type { CardHit, Draft } from '../../api/types'
import {
  OshibkaUPolya,
  RamkaShaga,
  svoistvaPolya,
  useShag,
  ZAGOLOVOK_SHAGA,
} from './RamkaShaga'

const POLYA = ['name'] as const

/** Пауза после последней буквы перед проверкой названия: на каждую букву
 *  спрашивать сервер незачем, а ждать дольше — повар уже тянется к «Далее». */
const ZADERZHKA_PROVERKI = 300

const POYASNENIE_ID = 'kartochka-nazvanie-poyasnenie'
const SPRAVOCHNIK_ID = 'kartochka-nazvanie-spravochnik'

function useOtlozhennoe(znachenie: string, zaderzhka: number): string {
  const [otlozhennoe, zadat] = useState(znachenie)
  useEffect(() => {
    const taimer = setTimeout(() => zadat(znachenie), zaderzhka)
    return () => clearTimeout(taimer)
  }, [znachenie, zaderzhka])
  return otlozhennoe
}

function kto(karta: CardHit): string {
  return karta.supplier ? `${karta.name} — ${karta.supplier}` : karta.name
}

/**
 * Шаг 3. Название — с проверкой, нет ли его уже.
 *
 * Точный дубль в листе писатель не запишет: повар узнаёт об этом здесь, а
 * не на последнем шаге, и «Далее» ждёт ответа проверки на то, что в поле
 * сейчас. Имя из справочника берётся касанием — тогда карточка склеится с
 * позицией справочника сама, без сверки шефом.
 */
export function ShagNazvanie({ chernovik }: { chernovik: Draft }) {
  const upravlenie = useShag(chernovik, POLYA)
  const [imya, zadatImya] = useState(chernovik.name)
  const tekst = imya.trim()
  const otlozhennoe = useOtlozhennoe(tekst, ZADERZHKA_PROVERKI)
  const proverka = useProverkaImeni(otlozhennoe)

  // Ответ проверки — о том, что в поле сейчас, а не о прежнем наборе.
  const proverenoTekushchee = otlozhennoe === tekst && (proverka.isSuccess || proverka.isError)
  const otvet = otlozhennoe === tekst ? proverka.data : undefined
  const dubl = otvet?.cards.exact[0]
  const kakVSpravochnike = otvet?.reference.exact.includes(tekst) ?? false
  const izSpravochnika = otvet
    ? [...otvet.reference.exact, ...otvet.reference.similar].filter((n) => n !== tekst)
    : []
  const pohozhie = otvet
    ? [
        ...otvet.cards.similar.map(kto),
        ...otvet.hidden.similar.map((karta) => `${kto(karta)} (убрана из таблицы)`),
      ]
    : []
  const ubrannaya = otvet?.hidden.exact[0]
  // Ждём паузы в наборе или ответа сервера — повар видит, почему «Далее»
  // ещё серая. Ответ не пришёл за 10 с — проверка считается несостоявшейся.
  const proveryaem = tekst !== '' && !proverenoTekushchee

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={tekst !== '' && proverenoTekushchee && dubl === undefined}
      onDalee={() => upravlenie.dalee({ name: tekst })}
      polya={{ name: tekst }}
    >
      <p id={POYASNENIE_ID} className="kartochka-poyasnenie">
        Как продукт будет называться в таблице.
      </p>
      <input
        className="kartochka-vvod"
        type="text"
        aria-labelledby={ZAGOLOVOK_SHAGA}
        {...svoistvaPolya(upravlenie, 'name', POYASNENIE_ID)}
        value={imya}
        maxLength={200}
        autoComplete="off"
        onChange={(sobytie) => zadatImya(sobytie.target.value)}
      />
      <OshibkaUPolya upravlenie={upravlenie} pole="name" />

      {/* Живая область: что проверка сказала, читалка экрана объявит сама,
          без перехода к тексту. Область есть всегда — иначе первое
          объявление теряется. */}
      <div role="status" className="kartochka-zhivaya">
        {proveryaem && <p className="kartochka-poyasnenie">Проверяем название…</p>}
        {proverka.isError && otlozhennoe === tekst && (
          <p className="kartochka-zamechanie">
            Не удалось проверить, есть ли уже такая карточка, — это проверится при отправке.
          </p>
        )}
        {kakVSpravochnike && (
          <p className="kartochka-horosho">
            Название как в справочнике — карточка свяжется с ним сама.
          </p>
        )}
      </div>

      {dubl && (
        <p className="kartochka-oshibka" role="alert">
          «{dubl.name}» уже есть в таблице
          {dubl.supplier ? ` (поставщик ${dubl.supplier})` : ''} — вторую карточку с этим
          названием не запишут. Измените название.
        </p>
      )}

      {izSpravochnika.length > 0 && (
        <>
          <p id={SPRAVOCHNIK_ID} className="kartochka-poyasnenie">
            Есть в справочнике — коснитесь, чтобы взять название оттуда: карточка свяжется с
            ним сама.
          </p>
          <ul className="kartochka-podskazki" aria-labelledby={SPRAVOCHNIK_ID}>
            {izSpravochnika.map((n) => (
              <li key={n}>
                <button type="button" onClick={() => zadatImya(n)}>
                  {n}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {ubrannaya && (
        <p className="kartochka-zamechanie">
          Карточку «{ubrannaya.name}» раньше убрали из таблицы. Новая с тем же названием вернёт
          на сайт старую — со старыми связями.
        </p>
      )}

      {pohozhie.length > 0 && (
        <div className="kartochka-zamechanie">
          <p>Похожие карточки — проверьте, не этот ли продукт:</p>
          <ul>
            {/* Номер в ключе: в листе бывают две строки с одним названием
                и поставщиком. */}
            {pohozhie.map((stroka, nomer) => (
              <li key={`${nomer}-${stroka}`}>{stroka}</li>
            ))}
          </ul>
        </div>
      )}
    </RamkaShaga>
  )
}
