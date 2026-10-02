import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { ApiError, api } from './client'
import type {
  CardOptions,
  Draft,
  DraftPatch,
  NameCheck,
  Shag,
  Submitted,
  VidFoto,
} from './types'

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
/** Чтение черновика: зависшее чтение держало бы и опрос распознавания —
 *  следующее не уходит, пока не кончилось прежнее. */
const SROK_CHTENIYA = 20_000
/** Проверка названия; не ответила — «Далее» доступна с замечанием. */
const SROK_PROVERKI_IMENI = 10_000
/** Одна попытка загрузки фото. */
export const SROK_ZAGRUZKI = 60_000
/** Распознавание: модель читает до трёх минут, столько же ждёт nginx. */
const SROK_RASPOZNAVANIYA = 180_000
/** Одна попытка отправки в таблицу: запись и перенос на сайт — обычно
 *  секунды; сервер отвечает не позже чем через ~10 с после записи. */
export const SROK_OTPRAVKI = 60_000
/** Сколько раз загрузка фото повторяется сама после обрыва связи. */
export const POVTOROV_ZAGRUZKI = 2
/** Сколько раз отправка повторяется сама после обрыва связи или срока. */
export const POVTOROV_OTPRAVKI = 2
/** Пауза перед повтором загрузки и отправки: дать сети вернуться. */
export const PAUZA_PERED_POVTOROM = 2_000
/** Как часто опрашивать черновик, пока распознавание идёт. */
export const OPROS_RASPOZNAVANIYA = 3_000

const KLYUCH_RASPOZNAVANIYA = ['kartochki', 'raspoznavanie'] as const

/**
 * Черновик повара. Пока распознавание этикетки идёт — в черновике
 * `running` или запрос на запуск ещё в пути, — он опрашивается сам раз в
 * 3 с: итог ляжет в черновик, даже если ответ на запуск до телефона не
 * дошёл (телефон уснул, связь пропала).
 */
export function useChernovik(): UseQueryResult<Draft | null> {
  const raspoznayotsya = useIsMutating({ mutationKey: KLYUCH_RASPOZNAVANIYA }) > 0
  return useQuery({
    queryKey: KLYUCH_CHERNOVIKA,
    queryFn: ({ signal }) =>
      api<Draft | null>('/cards/drafts/current', { signal, srok: SROK_CHTENIYA }),
    refetchInterval: (query) =>
      raspoznayotsya || query.state.data?.recognition_status === 'running'
        ? OPROS_RASPOZNAVANIYA
        : false,
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

/** Время правки черновика, мс. База отдаёт доли секунды до микросекунд —
 *  они обрезаются до миллисекунд: больше трёх знаков не всякий браузер
 *  разбирает. Не разобралось — NaN, и сравнение с ним ложно. */
function vremyaPravki(chernovik: Draft): number {
  return Date.parse(chernovik.updated_at.replace(/(\.\d{3})\d+/, '$1'))
}

/**
 * Ответ правки — черновик целиком, он и становится экраном. Перечитывание,
 * ушедшее до правки или пока она шла, несёт черновик старше правки: сначала
 * оно останавливается, потом ложится ответ. Остановка — здесь, при ответе, а
 * не перед запросом: так она ловит и перечитывание, начатое посреди правки.
 *
 * Ответ старше того, что уже лежит, не ложится: три фото продукта грузятся
 * разом, и опоздавший ответ первой загрузки (без второго фото) иначе
 * откатил бы экран. Сервер ставит время правки под блокировкой строки —
 * правки одного черновика идут по времени по очереди.
 */
async function polozhitChernovik(queries: QueryClient, chernovik: Draft): Promise<void> {
  await ostanovitPerechityvanie(queries)
  const lezhit = queries.getQueryData<Draft | null>(KLYUCH_CHERNOVIKA)
  if (lezhit?.id === chernovik.id && vremyaPravki(chernovik) < vremyaPravki(lezhit)) return
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
        if (prezhniy !== null) {
          await ostanovitPerechityvanie(queries)
          queries.setQueryData(KLYUCH_CHERNOVIKA, null)
        }
        throw oshibka
      }
    },
    // Как у правки: перечитывание, ушедшее до ответа, несёт прежний
    // черновик (или «нет черновика») — сначала оно останавливается.
    onSuccess: async (chernovik) => {
      await ostanovitPerechityvanie(queries)
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
 * Запрос с автоповторами после обрыва связи или срока без ответа.
 *
 * Мобильная сеть у плиты рвётся: обрыв — повод повторить, а не бросить.
 * Отказ сервера повтором не лечится — сразу наверх. `onPovtor` — номер
 * начатой попытки (2, 3): повар видит, что работа не брошена. Тем же
 * повторяется перенос карточки в справочник (`api/svarka.ts`).
 */
export async function sPovtoramiPriObryve<T>(
  zapros: () => Promise<T>,
  povtorov: number,
  onPovtor: (popytka: number) => void,
): Promise<T> {
  for (let popytka = 1; ; popytka += 1) {
    try {
      return await zapros()
    } catch (oshibka) {
      const obryv = oshibka instanceof ApiError && oshibka.status === 0
      if (!obryv || popytka > povtorov) throw oshibka
      onPovtor(popytka + 1)
      await pauza(PAUZA_PERED_POVTOROM)
    }
  }
}

/**
 * Фото в слот черновика — с двумя автоповторами того же фото.
 *
 * Повторяется то же уменьшенное фото: с камеры второй раз его не выбрать.
 * Отказ сервера (не JPEG, идёт отправка, хранилище недоступно) не
 * повторяется. Повтор безопасен: дошедшее без ответа фото просто заменится
 * тем же.
 */
function zagruzitFoto(
  { id, vid, foto }: ZagruzkaFoto,
  onPovtor: (popytka: number) => void,
): Promise<Draft> {
  return sPovtoramiPriObryve(
    () => {
      // Поле `photo` — так его ждёт сервер. Заголовок против подделки ставит
      // api(): PUT — изменяющий запрос.
      const forma = new FormData()
      forma.append('photo', foto, 'foto.jpg')
      return api<Draft>(`/cards/drafts/${encodeURIComponent(id)}/photos/${vid}`, {
        method: 'PUT',
        body: forma,
        srok: SROK_ZAGRUZKI,
      })
    },
    POVTOROV_ZAGRUZKI,
    onPovtor,
  )
}

/** Убрать фото из слота черновика: файл уходит в корзину хранилища. */
export function useUdalenieFoto() {
  const queries = useQueryClient()
  return useMutation({
    mutationFn: ({ id, vid }: { id: string; vid: VidFoto }) =>
      api<Draft>(`/cards/drafts/${encodeURIComponent(id)}/photos/${vid}`, {
        method: 'DELETE',
        srok: SROK_PRAVKI,
      }),
    onSuccess: (chernovik) => polozhitChernovik(queries, chernovik),
    onError: (oshibka) => perechitatPosle(queries, oshibka, OTSTALI),
  })
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
 * вышел срок. Это не ошибка распознавания — сервер дочитает этикетку и
 * положит итог в черновик. Поэтому после любого отказа черновик
 * перечитывается — до того, как отказ дойдёт до экрана: идёт — черновик
 * опрашивается сам (`useChernovik`), готово или не удалось — экран
 * показывает итог из черновика.
 *
 * 409 при том, что в черновике «идёт», — распознавание уже запущено
 * (другой вкладкой, до сна телефона): это не отказ, а ожидание. Прочие 409
 * («Карточка отправляется», «Фото этикетки заменили») — отказы с текстом.
 *
 * Контекст — `updated_at` черновика при запуске: итог, легший позже,
 * меняет его, и экран узнаёт итог, не дожидаясь ответа на запуск.
 */
export function useRaspoznavanie() {
  const queries = useQueryClient()
  return useMutation({
    mutationKey: KLYUCH_RASPOZNAVANIYA,
    onMutate: () => ({
      versiya: queries.getQueryData<Draft | null>(KLYUCH_CHERNOVIKA)?.updated_at ?? null,
    }),
    mutationFn: async (id: string) => {
      try {
        return await api<Draft>(`/cards/recognize/${encodeURIComponent(id)}`, {
          method: 'POST',
          srok: SROK_RASPOZNAVANIYA,
        })
      } catch (oshibka) {
        await queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })
        const chernovik = queries.getQueryData<Draft | null>(KLYUCH_CHERNOVIKA)
        const uzheIdyot =
          oshibka instanceof ApiError &&
          oshibka.status === 409 &&
          chernovik?.id === id &&
          chernovik.recognition_status === 'running'
        if (uzheIdyot) return chernovik
        throw oshibka
      }
    },
    onSuccess: (chernovik) => polozhitChernovik(queries, chernovik),
  })
}

export type Raspoznavanie = ReturnType<typeof useRaspoznavanie>

/** Сколько раз ждать собственную отправку, которая ещё пишет строку. */
export const OZHIDANIY_ZAPISI = 6
/** Пауза между ними: строка пишется 3–15 с, всего ждём около полуминуты. */
export const PAUZA_OZHIDANIYA_ZAPISI = 5_000

/** Ход отправки для экрана: какая попытка и не ждём ли свою же отправку. */
export type KhodOtpravki = { popytka: number; zhdyomZapisi: boolean }

/** Одна попытка отправки. Ответ 200, оборванный посреди тела, — это обрыв
 *  связи, а не отказ: строка, скорее всего, легла, и повтор её покажет. */
async function popytkaOtpravki(id: string): Promise<Submitted> {
  try {
    return await api<Submitted>(`/cards/drafts/${encodeURIComponent(id)}/submit`, {
      method: 'POST',
      srok: SROK_OTPRAVKI,
    })
  } catch (oshibka) {
    if (oshibka instanceof ApiError && oshibka.status >= 200 && oshibka.status < 300) {
      throw new ApiError(0, 'Ответ не дошёл целиком')
    }
    throw oshibka
  }
}

/**
 * Отправка с повторами.
 *
 * Обрыв или срок — повтор той же отправки, до двух раз. Если связь
 * оборвалась, пока сервер пишет строку, повтор через 2 с застаёт нашу же
 * первую отправку ещё идущей, и сервер отвечает 409 «попробуйте через N
 * минут» (N — предел отметки, до 17): живую отправку от умершей он не
 * отличит, а мы — отличим: была попытка без ответа. Тогда ждём её по 5 с,
 * до шести раз: закончилась — повтор отдаёт её строку (`already_written`);
 * упала и сняла отметку — повтор пишет обычным путём. Только потом — текст
 * сервера.
 */
async function otpravitSPovtorami(
  id: string,
  onKhod: (khod: KhodOtpravki) => void,
): Promise<Submitted> {
  let obryvov = 0
  let ozhidaniy = 0
  for (;;) {
    try {
      return await popytkaOtpravki(id)
    } catch (oshibka) {
      if (!(oshibka instanceof ApiError)) throw oshibka
      if (oshibka.status === 0 && obryvov < POVTOROV_OTPRAVKI) {
        obryvov += 1
        onKhod({ popytka: obryvov + 1, zhdyomZapisi: false })
        await pauza(PAUZA_PERED_POVTOROM)
        continue
      }
      const svoyaPishet = oshibka.status === 409 && oshibka.row === null && obryvov > 0
      if (svoyaPishet && ozhidaniy < OZHIDANIY_ZAPISI) {
        ozhidaniy += 1
        onKhod({ popytka: obryvov + 1, zhdyomZapisi: true })
        await pauza(PAUZA_OZHIDANIYA_ZAPISI)
        continue
      }
      throw oshibka
    }
  }
}

const NACHALO_OTPRAVKI: KhodOtpravki = { popytka: 1, zhdyomZapisi: false }

/**
 * «Отправить в таблицу».
 *
 * Отправка идемпотентна: ключ записи на сервере привязан к черновику, и
 * повтор отвечает той же строкой — второй в таблице не будет. Поэтому обрыв
 * связи и срок без ответа (60 с) — повод повторить ту же отправку самим
 * (`otpravitSPovtorami`). Отказ сервера не повторяется: дубль,
 * «отправляется», «таблица занята» повтор через 2 с не вылечит — повар
 * решит сам.
 *
 * Двойное касание даёт один запрос: вторая отправка не уходит, пока идёт
 * первая, — не только серой кнопкой, два касания успевают раньше
 * перерисовки. Защита живёт здесь, над шагами: она переживает и смену шага.
 *
 * Успех: черновика больше нет (он отправлен) — он перечитывается, а не
 * стирается из кэша сразу: экран «Записано» держится по ответу отправки, и
 * пустой кэш до него мелькнул бы экраном «Начать». Справочник и сверка
 * устарели — в них новая карточка, в подсказках — её поставщик и категория.
 */
export function useOtpravka() {
  const queries = useQueryClient()
  const [khod, zadatKhod] = useState(NACHALO_OTPRAVKI)
  const idyot = useRef(false)
  const mutatsiya = useMutation({
    onMutate: () => zadatKhod(NACHALO_OTPRAVKI),
    mutationFn: (chernovik: Draft) => otpravitSPovtorami(chernovik.id, zadatKhod),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: KLYUCH_CHERNOVIKA })
      void queries.invalidateQueries({ queryKey: ['reconciliation'] })
      void queries.invalidateQueries({ queryKey: ['ingredients'] })
      void queries.invalidateQueries({ queryKey: ['kartochki', 'varianty'] })
    },
    // 404 — черновик отправили или сбросили в другой вкладке; 422 — чего не
    // хватает, пересчитано по черновику на сервере.
    onError: (oshibka) => perechitatPosle(queries, oshibka, [...OTSTALI, 422]),
    onSettled: () => {
      idyot.current = false
    },
  })
  return {
    otpravit: (chernovik: Draft) => {
      if (idyot.current) return
      idyot.current = true
      mutatsiya.mutate(chernovik)
    },
    /** Отправка идёт — кнопки ждут. */
    idyot: mutatsiya.isPending,
    /** Отправляемый черновик — каким он был при нажатии. Пока отправка
     *  идёт, экран держит его итог: перечитывание может уже ответить
     *  «черновика нет» (строка легла, ответ ещё в пути). */
    chernovik: mutatsiya.isPending ? (mutatsiya.variables ?? null) : null,
    /** Номер идущей попытки (2 и 3 — повтор после обрыва) и ждём ли мы
     *  собственную отправку, которая ещё пишет строку. */
    khod,
    oshibka: mutatsiya.error,
    /** Когда ушла последняя отправка (мс), 0 — не уходила. Шаг итога
     *  показывает отказ, только если отправка ушла при нём: вернувшись к
     *  итогу после «Изменить название», повар не видит прежний отказ. */
    nachataV: mutatsiya.submittedAt,
    /** Карточка в листе — экран «Записано в таблицу». */
    otvet: mutatsiya.data ?? null,
    /** Забыть отправку: «Добавить ещё». */
    sbrosit: mutatsiya.reset,
  }
}

export type Otpravka = ReturnType<typeof useOtpravka>
