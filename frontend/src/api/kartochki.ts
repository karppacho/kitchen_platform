import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError, api } from './client'
import type { CardOptions, Draft, DraftPatch, NameCheck, Shag, VidFoto } from './types'

/** Шаги мастера по порядку — те же, что у сервера. По ним «Шаг N из 9». */
export const SHAGI: readonly Shag[] = [
  'supplier',
  'category',
  'name',
  'label',
  'review',
  'approval',
  'photos',
  'description',
  'summary',
]

export const NAZVANIYA_SHAGOV: Record<Shag, string> = {
  supplier: 'Поставщик',
  category: 'Категория',
  name: 'Название',
  label: 'Фото этикетки',
  review: 'Проверка',
  approval: 'Согласован ли продукт',
  photos: 'Фото продукта',
  description: 'Описание',
  summary: 'Итог',
}

/** Черновик — один на повара, поэтому и ключ один. Ответ каждой правки
 *  кладётся сюда же: шаг на экране — всегда шаг черновика на сервере. */
export const KLYUCH_CHERNOVIKA = ['kartochki', 'chernovik'] as const

const JSON_ZAGOLOVKI = { 'Content-Type': 'application/json' }

/*
 * Сроки, мс. Не дождались — ошибка связи, и кнопка снова доступна: ни одна
 * не остаётся серой без срока, пока телефон ловит сеть.
 */
/** Правка, начало и отмена черновика — быстрые ручки. */
const SROK_PRAVKI = 20_000
/** Проверка названия; не ответила — «Далее» доступна с замечанием. */
const SROK_PROVERKI_IMENI = 10_000
/** Одна попытка загрузки фото. */
export const SROK_ZAGRUZKI = 60_000
/** Распознавание: модель читает до трёх минут, столько же ждёт nginx. */
const SROK_RASPOZNAVANIYA = 180_000
/** Сколько раз загрузка фото повторяется сама после обрыва связи. */
export const POVTOROV_ZAGRUZKI = 2
/** Пауза перед повтором загрузки: дать сети вернуться. */
export const PAUZA_PERED_POVTOROM = 2_000
/** Как часто опрашивать черновик, пока распознавание идёт. */
export const OPROS_RASPOZNAVANIYA = 3_000

/** Черновик повара. Пока на сервере идёт распознавание этикетки, он
 *  опрашивается сам раз в 3 с: итог ляжет в черновик, даже если ответ на
 *  запуск распознавания до телефона не дошёл. */
export function useChernovik(): UseQueryResult<Draft | null> {
  return useQuery({
    queryKey: KLYUCH_CHERNOVIKA,
    queryFn: () => api<Draft | null>('/cards/drafts/current'),
    refetchInterval: (query) =>
      query.state.data?.recognition_status === 'running' ? OPROS_RASPOZNAVANIYA : false,
  })
}

export function useVarianty(): UseQueryResult<CardOptions> {
  return useQuery({
    queryKey: ['kartochki', 'varianty'],
    queryFn: () => api<CardOptions>('/cards/options'),
  })
}

/** Проверка названия. Пустое не проверяется: сервер ответил бы «ничего».
 *  Ушедшая проверка прежнего набора обрывается — её ответ уже не нужен. */
export function useProverkaImeni(imya: string): UseQueryResult<NameCheck> {
  return useQuery({
    queryKey: ['kartochki', 'imya', imya],
    queryFn: ({ signal }) =>
      api<NameCheck>(`/cards/name-check?name=${encodeURIComponent(imya)}`, {
        signal,
        srok: SROK_PROVERKI_IMENI,
      }),
    enabled: imya !== '',
  })
}

/**
 * Отказы, после которых экран отстал от сервера и черновик надо перечитать.
 *
 * 404 — черновика больше нет: его отправили или сбросили в другой вкладке.
 * Перечитав, экран сам покажет «Начать» вместо тупика «обновите страницу».
 * 401 — продление не помогло: перечитывание пройдёт через кэш запросов, и
 * оболочка вернёт на форму входа так же, как с любого экрана, — правки
 * идут мимо этого кэша и сами сессию не сбрасывают.
 */
const OTSTALI = [401, 404]

/** У начала есть ещё 409: черновик уже завели (вторая вкладка, двойное
 *  касание) — перечитав, повар выберет «Продолжить» или «Начать заново».
 *  409 при удалении (идёт отправка) перечитывание не меняет: черновик тот
 *  же, текст сервера остаётся на экране, сами не повторяем. */
const OTSTALI_PRI_NACHALE = [...OTSTALI, 409]

function perechitatPosle(queries: QueryClient, oshibka: unknown, kody: readonly number[]): void {
  if (oshibka instanceof ApiError && kody.includes(oshibka.status)) {
    void queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })
  }
}

/**
 * Остановить идущее перечитывание черновика. Телефон перечитывает его,
 * когда повар возвращается во вкладку, и опрашивает, пока идёт
 * распознавание; ответ, ушедший до правки и пришедший после, лёг бы поверх
 * ответа правки — и экран вернулся бы на шаг назад.
 */
function ostanovitPerechityvanie(queries: QueryClient): Promise<void> {
  return queries.cancelQueries({ queryKey: KLYUCH_CHERNOVIKA })
}

/**
 * Ответ правки — черновик целиком, он и становится экраном. Перечитывание,
 * ушедшее до правки или пока она шла, несёт черновик старше правки: сначала
 * оно останавливается, потом ложится ответ. Остановка — здесь, при ответе, а
 * не перед запросом: так она ловит и перечитывание, начатое посреди правки.
 */
async function polozhitChernovik(queries: QueryClient, chernovik: Draft): Promise<void> {
  await ostanovitPerechityvanie(queries)
  queries.setQueryData(KLYUCH_CHERNOVIKA, chernovik)
}

/** Правка черновика. Ответ — черновик целиком: он и становится экраном. */
export function usePravkaChernovika() {
  const queries = useQueryClient()
  return useMutation({
    mutationFn: ({ id, pravka }: { id: string; pravka: DraftPatch }) =>
      api<Draft>(`/cards/drafts/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: JSON_ZAGOLOVKI,
        body: JSON.stringify(pravka),
        srok: SROK_PRAVKI,
      }),
    onSuccess: (chernovik) => polozhitChernovik(queries, chernovik),
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI),
  })
}

/**
 * «Начать» и «Начать заново» — одним действием, чтобы у них был один отказ
 * на экране. У «заново» — id прежнего черновика: он удаляется (его фото
 * уходят в корзину), и сразу заводится новый.
 *
 * 404 при удалении — черновика уже нет (отправлен или сброшен в другой
 * вкладке): цель та же, заводим новый. Удаление прошло, а новый не завёлся
 * (обрыв связи) — в кэш кладётся «черновика нет»: экран покажет «Начать», а
 * не удалённый черновик, правка которого ответила бы 404.
 *
 * `onNachato` зовётся в одном вызове с записью в кэш: экран узнаёт, что
 * повар уже в работе, в ту же отрисовку, что и новый черновик, — без
 * мелькания «Продолжить или начать заново?» над только что начатым.
 */
export function useNachatChernovik(onNachato: (chernovik: Draft) => void) {
  const queries = useQueryClient()
  return useMutation({
    onMutate: () => ostanovitPerechityvanie(queries),
    mutationFn: async (prezhniy: string | null): Promise<Draft> => {
      if (prezhniy !== null) {
        try {
          await api<void>(`/cards/drafts/${encodeURIComponent(prezhniy)}`, {
            method: 'DELETE',
            srok: SROK_PRAVKI,
          })
        } catch (oshibka) {
          if (!(oshibka instanceof ApiError && oshibka.status === 404)) throw oshibka
        }
      }
      try {
        return await api<Draft>('/cards/drafts', { method: 'POST', srok: SROK_PRAVKI })
      } catch (oshibka) {
        if (prezhniy !== null) queries.setQueryData(KLYUCH_CHERNOVIKA, null)
        throw oshibka
      }
    },
    onSuccess: (chernovik) => {
      onNachato(chernovik)
      queries.setQueryData(KLYUCH_CHERNOVIKA, chernovik)
    },
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI_PRI_NACHALE),
  })
}

export type ZagruzkaFoto = { id: string; vid: VidFoto; foto: Blob }

function pauza(ms: number): Promise<void> {
  return new Promise((gotovo) => setTimeout(gotovo, ms))
}

/**
 * Фото в слот черновика — с двумя автоповторами того же фото.
 *
 * Мобильная сеть у плиты рвётся: обрыв или срок без ответа — повод
 * повторить, а не бросить загрузку. Повторяется то же уменьшенное фото: с
 * камеры второй раз его не выбрать. Отказ сервера (не JPEG, идёт отправка,
 * хранилище недоступно) повтором не лечится — сразу наверх. Повтор
 * безопасен: дошедшее без ответа фото просто заменится тем же.
 *
 * `onPovtor` — номер начатой попытки (2, 3): повар видит, что загрузка не
 * брошена.
 */
async function zagruzitFoto(
  { id, vid, foto }: ZagruzkaFoto,
  onPovtor: (popytka: number) => void,
): Promise<Draft> {
  for (let popytka = 1; ; popytka += 1) {
    // Поле `photo` — так его ждёт сервер. Заголовок против подделки ставит
    // api(): PUT — изменяющий запрос.
    const forma = new FormData()
    forma.append('photo', foto, 'foto.jpg')
    try {
      return await api<Draft>(`/cards/drafts/${encodeURIComponent(id)}/photos/${vid}`, {
        method: 'PUT',
        body: forma,
        srok: SROK_ZAGRUZKI,
      })
    } catch (oshibka) {
      const obryv = oshibka instanceof ApiError && oshibka.status === 0
      if (!obryv || popytka > POVTOROV_ZAGRUZKI) throw oshibka
      onPovtor(popytka + 1)
      await pauza(PAUZA_PERED_POVTOROM)
    }
  }
}

export function useZagruzkaFoto(onPovtor: (popytka: number) => void) {
  const queries = useQueryClient()
  return useMutation({
    mutationFn: (zagruzka: ZagruzkaFoto) => zagruzitFoto(zagruzka, onPovtor),
    onSuccess: (chernovik) => polozhitChernovik(queries, chernovik),
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI),
  })
}

/**
 * Распознать этикетку черновика: поля и замечания — в черновик.
 *
 * Ответ может не дойти: телефон уснул, nginx не дождался модели (504),
 * вышел срок, распознавание уже идёт (409). Это не ошибка распознавания —
 * сервер дочитает этикетку и положит итог в черновик. Поэтому после любого
 * отказа черновик перечитывается — до того, как отказ дойдёт до экрана: идёт
 * — черновик опрашивается сам (`useChernovik`), готово или не удалось — экран
 * показывает итог из черновика.
 */
export function useRaspoznavanie() {
  const queries = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api<Draft>(`/cards/recognize/${encodeURIComponent(id)}`, {
        method: 'POST',
        srok: SROK_RASPOZNAVANIYA,
      }),
    onSuccess: (chernovik) => polozhitChernovik(queries, chernovik),
    onError: () => queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA }),
  })
}

export type Raspoznavanie = ReturnType<typeof useRaspoznavanie>
