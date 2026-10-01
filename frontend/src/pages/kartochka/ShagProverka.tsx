import { useEffect, useState, type ChangeEvent } from 'react'

import { ApiError, SLISHKOM_CHASTO } from '../../api/client'
import type { Raspoznavanie } from '../../api/kartochki'
import type { Draft, DraftPatch } from '../../api/types'
import {
  OshibkaUPolya,
  RamkaShaga,
  idPolya,
  svoistvaPolya,
  useShag,
  type UpravlenieShagom,
} from './RamkaShaga'
import { PODPISI_ETIKETKI } from './ShagEtiketka'
import { useZagruzkaSnimka, ZagruzkaSnimka } from './ZagruzkaSnimka'

type PoleEtiketki =
  | 'label_name'
  | 'manufacturer'
  | 'composition'
  | 'protein'
  | 'fat'
  | 'carbs'
  | 'kcal'
  | 'shelf_life_sealed'
  | 'shelf_life_defrost'
  | 'shelf_life_after'
  | 'defrost_conditions'

type OpisaniePolya = {
  pole: PoleEtiketki
  nazvanie: string
  /** Предел сервера: длиннее он откажет, а молча обрезать набранное нельзя. */
  predel: number
  vid?: 'chislo' | 'mnogo'
}

const S_ETIKETKI: readonly OpisaniePolya[] = [
  { pole: 'label_name', nazvanie: 'Название по этикетке', predel: 500 },
  { pole: 'manufacturer', nazvanie: 'Изготовитель', predel: 500 },
  { pole: 'composition', nazvanie: 'Состав', predel: 4000, vid: 'mnogo' },
]

/** Белки, жиры, углеводы, ккал — строками, как написаны: число из них
 *  делает сервер («12,5», «250 ккал»), а не браузер. */
const KBZHU: readonly OpisaniePolya[] = [
  { pole: 'protein', nazvanie: 'Белки', predel: 100, vid: 'chislo' },
  { pole: 'fat', nazvanie: 'Жиры', predel: 100, vid: 'chislo' },
  { pole: 'carbs', nazvanie: 'Углеводы', predel: 100, vid: 'chislo' },
  { pole: 'kcal', nazvanie: 'Ккал', predel: 100, vid: 'chislo' },
]

const SROKI: readonly OpisaniePolya[] = [
  { pole: 'shelf_life_sealed', nazvanie: 'Срок в закрытой упаковке', predel: 300 },
  { pole: 'shelf_life_defrost', nazvanie: 'Срок после дефростации', predel: 300 },
  { pole: 'shelf_life_after', nazvanie: 'Срок после нарезки / фасовки', predel: 300 },
  { pole: 'defrost_conditions', nazvanie: 'Условия дефростации', predel: 500 },
]

/** Одиннадцать полей этикетки — их заполняет распознавание. */
const VSE = [...S_ETIKETKI, ...KBZHU, ...SROKI]
const POLYA: readonly string[] = VSE.map(({ pole }) => pole)

type Znacheniya = Record<PoleEtiketki, string>

function izChernovika(chernovik: Draft): Znacheniya {
  return Object.fromEntries(VSE.map(({ pole }) => [pole, chernovik[pole] ?? ''])) as Znacheniya
}

/** Набранное — в правку. Пустые КБЖУ — «нет данных» (null), не ноль. */
function vPravku(znacheniya: Znacheniya): DraftPatch {
  const pravka: DraftPatch = {}
  for (const { pole, vid } of VSE) {
    const znachenie = znacheniya[pole]
    pravka[pole] = vid === 'chislo' && znachenie.trim() === '' ? null : znachenie
  }
  return pravka
}

/**
 * Сколько ждать «идёт», опрашивая черновик, — страховка. Сервер считает
 * распознавание живым 230 с (170 с на модель и минута сверху), а зависшее
 * дольше сам выдаёт в черновике как «не удалось». Если и это не сработало
 * (старый сервер), экран не ждёт вечно.
 *
 * Отсчёт — от показа шага: время запуска сервер в черновике не отдаёт, да
 * и часы телефона с серверными могут расходиться на минуты.
 */
const PREDEL_OZHIDANIYA = 240_000

/** Через сколько повтор после «слишком часто» nginx снова имеет смысл. */
const MINUTA = 60_000

/** Условие держится дольше `ms`. Сбрасывается, как только перестало. */
function useDolgo(uslovie: boolean, ms: number): boolean {
  const [dolgo, zadatDolgo] = useState(false)
  useEffect(() => {
    if (!uslovie) return
    const taimer = setTimeout(() => zadatDolgo(true), ms)
    return () => {
      clearTimeout(taimer)
      zadatDolgo(false)
    }
  }, [uslovie, ms])
  return dolgo && uslovie
}

const NE_DOZHDALIS =
  'Распознавание не ответило. Нажмите «Распознать ещё раз» или заполните поля вручную.'

/** Что сказать о распознавании, когда оно не идёт. */
function soobshchenieORaspoznavanii(chernovik: Draft, otkaz: unknown): string | null {
  const status = chernovik.recognition_status
  const kod = otkaz instanceof ApiError ? otkaz.status : null
  // Ответ не дошёл (сон телефона, 504 nginx, срок): это не ошибка — итог в
  // черновике, он перечитан.
  if (kod === 0 || kod === 504) {
    if (status === 'failed') return chernovik.recognition_error
    if (status === null) return NE_DOZHDALIS
    return null
  }
  // 409, 429, 502, 503 и прочие — текст сервера: «Карточка отправляется»,
  // лимит, модель не ответила, не настроено. Он о том, что случилось сейчас,
  // и важнее прежнего итога в черновике («готово» или прежняя ошибка). У 502
  // тот же текст лежит и в черновике — показываем один раз. 409 «уже идёт»
  // сюда не доходит: это ожидание, а не отказ (`useRaspoznavanie`).
  if (otkaz instanceof Error) return otkaz.message
  return status === 'failed' ? chernovik.recognition_error : null
}

/**
 * Шаг 5. Проверка распознанного — 11 полей этикетки.
 *
 * Пока распознавание идёт, полей нет: итог лёг бы поверх набранного. Ответ
 * на запуск может не прийти (телефон уснул, nginx не дождался) — черновик
 * опрашивается раз в 3 с, пока в нём «идёт» или запрос на запуск в пути.
 * Не вышло — поля заполняются вручную, «Распознать ещё раз» — если повтор
 * имеет смысл (не дневной лимит и не «не настроено»; после «слишком часто»
 * — через минуту). «Переснять» — новое фото и новое распознавание, шаг тот же.
 */
export function ShagProverka({
  chernovik,
  raspoznavanie,
}: {
  chernovik: Draft
  raspoznavanie: Raspoznavanie
}) {
  const upravlenie = useShag(chernovik, POLYA)
  const zagruzka = useZagruzkaSnimka(chernovik, 'label', {
    pered: () => raspoznavanie.reset(),
    posle: () => raspoznavanie.mutate(chernovik.id),
  })
  const status = chernovik.recognition_status
  // Отказ и ожидание — только этого черновика: «Начать заново» заводит новый.
  const nashe = raspoznavanie.variables === chernovik.id
  const vPuti = nashe && raspoznavanie.isPending
  // Ответ на запуск ещё в пути (телефон спал, сеть медленная), а итог уже
  // лёг в черновик — черновик правдивее: после запуска он поменялся.
  const itogVChernovike =
    vPuti &&
    (status === 'done' || status === 'failed') &&
    chernovik.updated_at !== raspoznavanie.context?.versiya
  const zhdyom = (vPuti && !itogVChernovike) || status === 'running'
  const dolgo = useDolgo(zhdyom, PREDEL_OZHIDANIYA)
  const idyot = zhdyom && !dolgo
  const otkaz = nashe ? raspoznavanie.error : null
  const kod = otkaz instanceof ApiError ? otkaz.status : null
  const soobshchenie = idyot ? null : soobshchenieORaspoznavanii(chernovik, otkaz)
  // «Слишком часто» nginx проходит за минуту; дневной лимит (тот же код
  // 429, свой текст) и «не настроено» повтором не лечатся.
  const chastota = kod === 429 && otkaz instanceof Error && otkaz.message === SLISHKOM_CHASTO
  const minutaProshla = useDolgo(chastota, MINUTA)
  const bezPovtora = kod === 503 || (kod === 429 && !minutaProshla)
  const mozhnoRaspoznat = chernovik.photos.label && !idyot && !bezPovtora && !zagruzka.idyot
  // До первого запуска — просто «Распознать»: «ещё раз» — когда уже было.
  const zapuskali = status !== null || (nashe && !raspoznavanie.isIdle)

  // Поля — с черновика. Сервер его поменял (лёг итог распознавания, заменили
  // этикетку) — поля берутся заново: на экране то, что в черновике.
  const [znacheniya, zadatZnacheniya] = useState(() => izChernovika(chernovik))
  const [versiya, zadatVersiyu] = useState(chernovik.updated_at)
  if (versiya !== chernovik.updated_at) {
    zadatVersiyu(chernovik.updated_at)
    zadatZnacheniya(izChernovika(chernovik))
  }
  const izmenit = (pole: PoleEtiketki, znachenie: string) =>
    zadatZnacheniya((prezhnie) => ({ ...prezhnie, [pole]: znachenie }))

  return (
    <RamkaShaga
      upravlenie={upravlenie}
      mozhnoDalee={!idyot}
      onDalee={() => upravlenie.dalee(vPravku(znacheniya))}
      polya={idyot ? undefined : vPravku(znacheniya)}
      zhdyom={zagruzka.idyot}
    >
      <ZagruzkaSnimka
        chernovik={chernovik}
        vid="label"
        zagruzka={zagruzka}
        podpisi={PODPISI_ETIKETKI}
        zablokirovano={idyot}
        malenkoe
      />
      {mozhnoRaspoznat && (
        <div className="kartochka-knopki">
          <button type="button" onClick={() => raspoznavanie.mutate(chernovik.id)}>
            {zapuskali ? 'Распознать ещё раз' : 'Распознать'}
          </button>
        </div>
      )}

      <div role="status" className="kartochka-zhivaya">
        {idyot && (
          <p className="kartochka-zhdyom">
            Распознаём этикетку — это может занять до трёх минут. Можно отойти: результат
            сохранится в карточке.
          </p>
        )}
        {dolgo && (
          <p className="kartochka-zamechanie">
            Распознавание идёт дольше обычного — похоже, оно прервалось. Заполните поля вручную
            или нажмите «Распознать ещё раз».
          </p>
        )}
        {!zhdyom && chernovik.recognition_status === 'done' && (
          <p className="kartochka-poyasnenie">
            Проверьте, что распознано с этикетки, и исправьте, если нужно.
          </p>
        )}
      </div>
      {soobshchenie && (
        <p className="kartochka-oshibka" role="alert">
          {soobshchenie}
        </p>
      )}

      {!idyot && (
        <>
          {chernovik.warnings.length > 0 && (
            <div className="kartochka-zamechanie">
              <p>Проверьте:</p>
              <ul>
                {chernovik.warnings.map((zamechanie, nomer) => (
                  <li key={`${nomer}-${zamechanie}`}>{zamechanie}</li>
                ))}
              </ul>
            </div>
          )}
          <Polya spisok={S_ETIKETKI} {...{ znacheniya, izmenit, upravlenie }} />
          <fieldset className="kartochka-gruppa">
            <legend>КБЖУ на 100 г</legend>
            <Polya spisok={KBZHU} {...{ znacheniya, izmenit, upravlenie }} />
          </fieldset>
          <fieldset className="kartochka-gruppa">
            <legend>Сроки</legend>
            <Polya spisok={SROKI} {...{ znacheniya, izmenit, upravlenie }} />
          </fieldset>
        </>
      )}
    </RamkaShaga>
  )
}

function Polya({
  spisok,
  znacheniya,
  izmenit,
  upravlenie,
}: {
  spisok: readonly OpisaniePolya[]
  znacheniya: Znacheniya
  izmenit: (pole: PoleEtiketki, znachenie: string) => void
  upravlenie: UpravlenieShagom
}) {
  return (
    <>
      {spisok.map(({ pole, nazvanie, predel, vid }) => (
        <Pole key={pole} {...{ pole, nazvanie, predel, vid, znacheniya, izmenit, upravlenie }} />
      ))}
    </>
  )
}

function Pole({
  pole,
  nazvanie,
  predel,
  vid,
  znacheniya,
  izmenit,
  upravlenie,
}: OpisaniePolya & {
  znacheniya: Znacheniya
  izmenit: (pole: PoleEtiketki, znachenie: string) => void
  upravlenie: UpravlenieShagom
}) {
  const svoistva = {
    className: 'kartochka-vvod',
    value: znacheniya[pole],
    maxLength: predel,
    autoComplete: 'off',
    ...svoistvaPolya(upravlenie, pole),
    onChange: (sobytie: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      izmenit(pole, sobytie.target.value),
  }
  return (
    <div className="kartochka-pole">
      <label htmlFor={idPolya(pole)}>{nazvanie}</label>
      {vid === 'mnogo' ? (
        <textarea rows={4} {...svoistva} />
      ) : (
        <input type="text" inputMode={vid === 'chislo' ? 'decimal' : undefined} {...svoistva} />
      )}
      <OshibkaUPolya upravlenie={upravlenie} pole={pole} />
    </div>
  )
}
