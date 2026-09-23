import { useEffect, useRef, useState } from 'react'

// Пауза после последней буквы, прежде чем запрос уйдёт на сервер. На каждую
// букву слать нельзя — 130 строк и печатающий шеф дадут запрос на каждый
// символ; но и заставлять ждать секунду после того, как он замер, тоже
// раздражает. 300 мс — между «сразу» и «через раздумье».
const ZADERZHKA_POISKA = 300

/**
 * Локальный ввод поиска с задержкой перед уходом в адрес.
 *
 * Общее для всех экранов со списками (справочник ингредиентов, блюда):
 * манера поиска обязана быть одинаковой. Поле не залипает на каждой букве,
 * ожидая подтверждения от адресной строки, — в адрес значение уходит с
 * задержкой; если же адрес поменялся не из этого поля (открыли присланную
 * ссылку, нажали «назад»), поле подхватывает значение обратно.
 */
export function useOtlozhennyiPoisk(
  search: string,
  otpravit: (znachenie: string) => void,
): [string, (znachenie: string) => void] {
  const [vvod, zadatVvod] = useState(search)

  // Таймер зовёт последнюю версию колбэка, а не ту, что была при нажатии
  // клавиши. Колбэк экрана строит адрес из параметров своего рендера, и
  // старая версия за 300 мс успевает устареть: статус, выбранный в эту
  // паузу, запись поиска стирала бы. Функциональная форма setSearchParams
  // тут не спасает — в React Router 6 она получает параметры того же
  // старого рендера.
  const posledniyOtpravit = useRef(otpravit)
  useEffect(() => {
    posledniyOtpravit.current = otpravit
  })

  useEffect(() => {
    zadatVvod(search)
  }, [search])

  useEffect(() => {
    if (vvod === search) return
    const taimer = setTimeout(() => posledniyOtpravit.current(vvod), ZADERZHKA_POISKA)
    return () => clearTimeout(taimer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vvod])

  return [vvod, zadatVvod]
}
