import type { ChangeEvent } from 'react'

import './fotoVybor.css'

/**
 * Выбор фото: камера или галерея.
 *
 * Два поля файла под видом кнопок. У первого `capture` — на телефоне сразу
 * открывается камера (задняя), на компьютере — обычный выбор файла. У
 * второго `capture` нет — телефон предлагает галерею: этикетку могли снять
 * заранее. Поле само остаётся в разметке (невидимое, но доступное с
 * клавиатуры и читалке экрана): подпись-кнопка лишь его открывает.
 */
export function FotoVybor({
  kamera,
  galereya,
  onVybrano,
  zablokirovano = false,
  glavnaya = true,
}: {
  /** Подпись кнопки камеры: «Сфотографировать этикетку», «Переснять». */
  kamera: string
  /** Подпись кнопки галереи; нет — только камера. */
  galereya?: string
  onVybrano: (fail: File) => void
  zablokirovano?: boolean
  /** Камера — главное действие экрана (акцентом). Нет — когда главное
   *  другое, «Далее»: «Переснять» не должна с ней спорить. */
  glavnaya?: boolean
}) {
  function vybrano(sobytie: ChangeEvent<HTMLInputElement>) {
    const fail = sobytie.target.files?.[0]
    // Сброс: выбрать тот же файл ещё раз — снова событие, а не тишина.
    sobytie.target.value = ''
    if (fail) onVybrano(fail)
  }

  return (
    <div className="foto-vybor">
      <label className={glavnaya ? 'foto-vybor-knopka foto-vybor-glavnaya' : 'foto-vybor-knopka'}>
        <input
          className="foto-vybor-pole"
          type="file"
          accept="image/*"
          capture="environment"
          disabled={zablokirovano}
          onChange={vybrano}
        />
        {kamera}
      </label>
      {galereya && (
        <label className="foto-vybor-knopka">
          <input
            className="foto-vybor-pole"
            type="file"
            accept="image/*"
            disabled={zablokirovano}
            onChange={vybrano}
          />
          {galereya}
        </label>
      )}
    </div>
  )
}
