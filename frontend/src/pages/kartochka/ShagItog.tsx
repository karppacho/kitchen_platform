import { useEffect, useRef, useState } from 'react'

import { ApiError, NE_POLUCHILOS } from '../../api/client'
import { POVTOROV_OTPRAVKI, type Otpravka } from '../../api/kartochki'
import type { Draft, Shag, Submitted, VidFoto } from '../../api/types'
import { RamkaShaga, useShag, type UpravlenieShagom } from './RamkaShaga'

/** Где заполняется то, чего не хватает для отправки, — по названию, каким
 *  его отдаёт сервер в `missing`. */
const GDE_ZAPOLNYAETSYA: Readonly<Record<string, Shag>> = {
  Поставщик: 'supplier',
  Категория: 'category',
  Название: 'name',
  'Фото этикетки': 'label',
  'Согласован ли продукт': 'approval',
}

const FOTO: readonly { vid: VidFoto; nazvanie: string }[] = [
  { vid: 'label', nazvanie: 'этикетка' },
  { vid: 'package', nazvanie: 'в упаковке' },
  { vid: 'before', nazvanie: 'до обработки' },
  { vid: 'after', nazvanie: 'после обработки' },
]

/** Все попытки оборвались или не дождались ответа. Строка могла и лечь —
 *  повтор это узнает и второй не напишет. */
const NE_DOSHLO =
  'Ответ не дошёл — проверьте связь и нажмите «Отправить ещё раз». Черновик сохранён, ' +
  'второй строки в таблице не будет.'

/** Сбой сервера без текста — страница nginx (сервер перезапускается). */
const SERVER_NE_OTVETIL =
  'Сервер не ответил. Черновик сохранён — нажмите «Отправить ещё раз» через минуту: ' +
  'второй строки в таблице не будет.'

/**
 * Что сказать об отказе отправки. Текст сервера написан для повара — что
 * случилось и что делать: «таблица занята», «колонки сдвинулись», «черновик
 * сохранён», — и показывается как есть. Свой — только когда сервер ничего
 * не сказал: связь оборвалась или ответил nginx.
 */
function tekstOtkaza(oshibka: unknown): string {
  if (!(oshibka instanceof ApiError) || oshibka.status === 0) return NE_DOSHLO
  if (oshibka.status >= 500 && oshibka.message === NE_POLUCHILOS) return SERVER_NE_OTVETIL
  return oshibka.message
}

/**
 * Шаг 9. Итог: что уйдёт в таблицу и «Отправить в таблицу».
 *
 * Чего не хватает — видно сразу, с переходом к шагу, где это заполняется;
 * пока не хватает, отправить нельзя. Отказ сервера — его текстом: дубль
 * названия ведёт на шаг названия, «не хватает» — к недостающему, прочее
 * («таблица занята», «черновик сохранён») — повтор позже той же кнопкой.
 * Пока идёт отправка, все кнопки ждут.
 */
export function ShagItog({ chernovik, otpravka }: { chernovik: Draft; otpravka: Otpravka }) {
  const upravlenie = useShag(chernovik)
  // Отказ — только отправки, ушедшей при этом показе шага: вернувшись сюда
  // после «Изменить название», повар не видит прежний отказ.
  const [pokazan] = useState(() => Date.now())
  const otkaz = otpravka.nachataV >= pokazan ? otpravka.oshibka : null
  const otkazPoPolyam = otkaz instanceof ApiError && otkaz.missing ? otkaz : null
  const nedostayot = otkazPoPolyam?.missing ?? chernovik.missing
  const dubl = otkaz instanceof ApiError && otkaz.status === 409 && otkaz.row !== null
  const zanyato = otpravka.idyot || upravlenie.zanyato

  const perejti = (shag: Shag) => upravlenie.dalee({}, shag)
  // У отбракованного не было фото продукта и описания — «Назад» к вопросу.
  const nazadNa: Shag = chernovik.approval === 'Отбракован' ? 'approval' : 'description'
  const upravlenieItoga: UpravlenieShagom = { ...upravlenie, nazad: () => perejti(nazadNa) }

  return (
    <RamkaShaga
      upravlenie={upravlenieItoga}
      mozhnoDalee={nedostayot.length === 0}
      onDalee={() => otpravka.otpravit(chernovik.id)}
      tekstDalee={otkaz ? 'Отправить ещё раз' : 'Отправить в таблицу'}
      zhdyom={otpravka.idyot}
    >
      <Svodka chernovik={chernovik} />

      {nedostayot.length > 0 && (
        <div className="kartochka-zamechanie">
          <p role={otkazPoPolyam ? 'alert' : undefined}>
            {otkazPoPolyam?.message ??
              `Чтобы отправить карточку, заполните: ${nedostayot.join(', ')}`}
          </p>
          <ul className="kartochka-podskazki kartochka-perekhody">
            {nedostayot.map((pole) => {
              const shag = GDE_ZAPOLNYAETSYA[pole]
              return (
                shag && (
                  <li key={pole}>
                    <button type="button" disabled={zanyato} onClick={() => perejti(shag)}>
                      {pole}
                    </button>
                  </li>
                )
              )
            })}
          </ul>
        </div>
      )}

      <div role="status" className="kartochka-zhivaya">
        {otpravka.idyot && (
          <p className="kartochka-zhdyom">
            {otpravka.popytka === 1
              ? 'Отправляем карточку в таблицу — это может занять до минуты.'
              : `Связь прервалась — отправляем ещё раз (попытка ${otpravka.popytka} из ${
                  POVTOROV_OTPRAVKI + 1
                }) — второй строки в таблице не будет.`}
          </p>
        )}
      </div>

      {otkaz && !otkazPoPolyam && (
        <div className="kartochka-oshibka">
          <p role="alert">{tekstOtkaza(otkaz)}</p>
          {dubl && (
            <div className="kartochka-knopki">
              <button type="button" disabled={zanyato} onClick={() => perejti('name')}>
                Изменить название
              </button>
            </div>
          )}
        </div>
      )}
    </RamkaShaga>
  )
}

/** Что уйдёт в таблицу — главное, чтобы повар узнал свою карточку. */
function Svodka({ chernovik }: { chernovik: Draft }) {
  const foto = FOTO.filter(({ vid }) => chernovik.photos[vid]).map(({ nazvanie }) => nazvanie)
  const stroki: [string, string][] = [
    ['Поставщик', chernovik.supplier],
    ['Категория', chernovik.category],
    ['Название', chernovik.name],
    ['Согласован', chernovik.approval ?? ''],
    ['Фото', foto.join(', ')],
  ]
  if (chernovik.description.trim() !== '') stroki.push(['Описание', chernovik.description])

  return (
    <ul className="kartochka-svodka-spisok" aria-label="Карточка">
      {stroki.map(([nazvanie, znachenie]) => (
        <li key={nazvanie}>
          <span className="kartochka-poyasnenie">{nazvanie}</span>
          {znachenie === '' ? <span className="kartochka-poyasnenie">—</span> : <b>{znachenie}</b>}
        </li>
      ))}
    </ul>
  )
}

/**
 * Карточка в таблице: «Записано в таблицу, строка N».
 *
 * Оговорки сервера — готовыми фразами, как есть. Правки, не попавшие в
 * лист, и прежняя попытка, при которой таблицу меняли, — для шефа: они
 * выделены. «На сайт пока не перенесена» — не тревога: строка в листе,
 * сайт подтянет её сам.
 */
export function Zapisano({
  otvet,
  onEshche,
  zanyato,
}: {
  otvet: Submitted
  onEshche: () => void
  zanyato: boolean
}) {
  const zagolovok = useRef<HTMLHeadingElement>(null)
  // Кнопка, на которой был фокус, исчезла вместе с шагом — фокус на итог.
  useEffect(() => zagolovok.current?.focus(), [])
  const dlyaShefa = otvet.not_written.length > 0 || otvet.shifted !== null

  return (
    <div className="kartochka">
      <h2 ref={zagolovok} tabIndex={-1}>
        {`Записано в таблицу, строка ${otvet.row}`}
      </h2>
      <p className="kartochka-svodka">
        <b>{otvet.name}</b>
      </p>
      {otvet.notes.length > 0 && (
        <ul className={dlyaShefa ? 'kartochka-zamechanie' : 'kartochka-poyasnenie'}>
          {otvet.notes.map((zametka) => (
            <li key={zametka}>{zametka}</li>
          ))}
        </ul>
      )}
      <div className="kartochka-knopki">
        <button type="button" className="kartochka-glavnaya" onClick={onEshche} disabled={zanyato}>
          Добавить ещё
        </button>
      </div>
    </div>
  )
}
