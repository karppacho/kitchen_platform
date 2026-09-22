import { useEffect, useState } from 'react'

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

  useEffect(() => {
    zadatVvod(search)
  }, [search])

  useEffect(() => {
    if (vvod === search) return
    const taimer = setTimeout(() => otpravit(vvod), ZADERZHKA_POISKA)
    return () => clearTimeout(taimer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vvod])

  return [vvod, zadatVvod]
}
