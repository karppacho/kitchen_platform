# Сортировка и фильтры по всем колонкам справочника и блюд — спека

Этап 3 плана работ после фазы 2. План исполнения: `docs/superpowers/plans/2026-09-27-filtry-sortirovka.md`.

## 1. Зачем

Этап 2 (синхронизация раз в 5 минут) выложен 27.09.2026. Следующий по порядку работ (ROADMAP, п. 3;
решение Александра 23.09) — «фильтры и сортировка по всем колонкам справочника и блюд, как в
Google-таблицах». Сейчас на экранах есть только поиск по названию (серверный) и выпадающий статус.
Долги, которые этап закрывает: сортировка блюд по марже (отрицательная маржа −399,3 не
сравнивалась), выбранный статус пропадает из списка во время поиска, «← Блюда» теряет фильтры.

**Решение Александра 27.09:** числовые колонки сортируются, а фильтры у них — готовые галочки по
смыслу («нет цены», «себестоимость выше цены», «есть замечания», «нет веса штуки»), без условий
«больше/меньше». У текстовых колонок (категория, единица, статус) — список значений с галочками и
числом строк, как в Google-таблицах.

Итог: на широком экране у каждого заголовка кнопка сортировки (по возрастанию → по убыванию →
выключено) и значок фильтра со всплывающей панелью галочек; на телефоне — выбор «Сортировка» и
кнопка «Фильтры» с теми же группами; над таблицей — поиск, «Найдено: N из M», фишки активных
фильтров и «Сбросить всё». Всё состояние — в адресе: ссылкой можно поделиться, «← Блюда»
возвращает к тому же виду.

**Tech Stack:** React 18.3 + TypeScript, TanStack Query 5, react-router-dom 6.30, vitest + Testing
Library + msw + jsdom 25. Бэкенд не меняется.

## 2. Решения

1. **Всё считается в браузере.** Каждый экран один раз берёт полный список: ингредиенты — с явным
   `limit=500` и заметным предупреждением, если ответ упёрся в лимит; блюда — как есть (лимита
   нет). Поиск переезжает на клиент: по названию и id, без учёта регистра, «ё» = «е», мгновенно, без
   «Загрузки…». Ключ запроса не зависит от поиска — пропадает долг «статус исчезает».
2. **Сортировка:** щелчок по заголовку — по возрастанию → по убыванию → выключено (порядок ответа
   сервера). Текст — `Intl.Collator('ru', {numeric: true, sensitivity: 'base'})` (id «12» < «123»).
   Деньги и проценты — точное сравнение десятичных строк со знаком, без перевода в float. Пустые
   значения — всегда в конце, в обе стороны. Сортировка стабильная. `aria-sort` на отсортированном
   заголовке.
3. **Фильтры:** внутри колонки — ИЛИ, между колонками — И. Варианты и счётчики колонки считаются по
   строкам, прошедшим поиск и фильтры **других** колонок (как в Google-таблицах); выбранное
   значение, у которого сейчас 0 строк, остаётся видимым и отмеченным. Пустое значение — «(пусто)».
   Готовые условия видны всегда.
4. **Адрес:** `?search=соус&sort=-margin&category=Блюдо&category=Соус-топпинг&uc=vyshe`. Ключи —
   `key` колонок (ASCII), повтор параметра на каждое значение, значения по умолчанию не пишутся,
   порядок детерминированный, чужие параметры сохраняются, старые ссылки `?status=…&search=…`
   работают. Запись — `{replace: true}`.
5. **Панель фильтра на широком экране — портал в `document.body`** с координатами документа:
   внутри `th` её обрезала бы прокрутка обёртки таблицы, а липкие `th` соседних колонок легли бы
   поверх. Popover API и `<dialog>.showModal()` отпадают: в jsdom 25 их нет. Фокус на первой
   галочке, Tab по кругу, Escape / «Готово» / щелчок снаружи закрывают, фокус возвращается на значок.
6. **Узкий экран (< 1080 px)** — заголовков там нет: выбор «Сортировка» (у маржи — «сначала
   худшая / лучшая») и кнопка «Фильтры (k)», раскрывающая встроенную панель с теми же группами.
   Один компонент группы галочек на обе ширины.
7. **Пустой результат не прячет управление:** на широком экране при 0 строках шапка остаётся.
8. **Недостающие колонки** (только на широком): у ингредиентов «Ед.» после «Категории», у блюд
   «Статус» после «Категории» («Замечания» остаются последней колонкой — на этом держится тест).
9. **Возврат из карточки:** строка передаёт адрес списка в `state` навигации; все три ссылки
   «← Блюда» (карточка, 404, 410) возвращают к нему. Адрес собирается из текущего состояния, а не
   из `location.search`, — иначе потерялся бы поиск, ещё не записанный в адрес.

## 3. Ограничения

- Только фронтенд; бэкенд не трогать (параметр `limit` до 500 уже есть).
- Никаких новых зависимостей. Никаких `Number()`, `parseFloat`, `parseInt` над деньгами и
  процентами; десятичные числа приходят строками и так и сравниваются.
- Значения фильтров берутся из ответа, в код не зашиваются (реальные статусы «активное»/«архив»
  отличаются от фикстур «активный»/«архивный»).
- Имена — транслитом, как в соседних файлах; тексты для людей — по-русски; SVG-иконки в стиле
  `ui/Icons.tsx`, без эмодзи; только токены из `styles/tokens.css`; плотность 25–30 строк на
  1920×1080 не ухудшать (иконки 14 px).
- Минимальная ширина 360 px, горизонтальной прокрутки страницы нет. Клавиатура работает на обеих
  ширинах.
- Ключи запросов сохраняют префиксы `['ingredients']` и `['dishes']` — по ним строка свежести
  перезапрашивает экраны. Сверка продолжает делить кэш со справочником через `useIngredients()`.
- Ловушка react-router 6 (Ruling 74 фазы 2): функциональная форма `setSearchParams` видит старые
  параметры — все записи в адрес идут через одну функцию с ref на свежие параметры.
- `domain/` не импортирует из `ui/`.
- Блокирующий CI — до пяти минут. В репозитории не упоминать название сети и юрлица клиента.
- Windows: файлы — только инструментами Write/Edit, после правок `git ls-files --eol` → `w/lf`.
  Коммиты по-русски, последняя строка `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## 4. Устройство

**`domain/sravnenie.ts`**
```ts
export function razobratDesyatichnoe(s: string): { minus: boolean; tsel: string; drob: string } | null
// ^-?\d+(\.\d+)?$; tsel без ведущих нулей ('' → '0'), drob без хвостовых; '-0' → minus:false
export function sravnitDesyatichnye(a: string, b: string): -1 | 0 | 1
// знак → длина целой → целая → дробная (дополненная нулями); у отрицательных — наоборот;
// неразбираемое больше любого числа
export function sravnitTekst(a: string, b: string): number   // один Intl.Collator на модуль
```
**`domain/poisk.ts`** — `normalizovat(s)` (нижний регистр по-русски, «ё» → «е»),
`sovpadaet(polya: readonly string[], zapros: string): boolean` (пустой запрос — всё).

**`domain/tablitsa.ts`**
```ts
export type Napravlenie = 'vozr' | 'ubyv'
export type Sortirovka = { kolonka: string; napravlenie: Napravlenie }
export type SposobSortirovki<T> =
  | { vid: 'tekst'; znachenie: (r: T) => string | null; podpisi?: readonly [string, string] }
  | { vid: 'chislo'; znachenie: (r: T) => string | number | null; podpisi?: readonly [string, string] }
export type Uslovie<T> = { kod: string; podpis: string; podhodit: (r: T) => boolean }
export type SposobFiltra<T> =
  | { vid: 'znacheniya'; znachenie: (r: T) => string }
  | { vid: 'usloviya'; usloviya: readonly Uslovie<T>[] }
export type OpisanieKolonki<T> = { key: string; title: string; sort?: SposobSortirovki<T>; filtr?: SposobFiltra<T> }
export type Vybor = Readonly<Record<string, readonly string[]>>
export type SostoyanieTablitsy = { poisk: string; sortirovka: Sortirovka | null; vybor: Vybor }
export type Variant = { kod: string; podpis: string; schyot: number; vybran: boolean }
export type GruppaFiltra = { kolonka: string; zagolovok: string; varianty: Variant[]; aktivna: boolean }
export function sortirovat<T>(stroki: readonly T[], s: SposobSortirovki<T>, n: Napravlenie): T[]
export function podhodit<T>(r: T, kolonki: readonly OpisanieKolonki<T>[], vybor: Vybor, krome?: string): boolean
export function sobratGruppy<T>(poslePoiska: readonly T[], kolonki: readonly OpisanieKolonki<T>[], vybor: Vybor): GruppaFiltra[]
export function primenit<T>(stroki: readonly T[], kolonki: readonly OpisanieKolonki<T>[], s: SostoyanieTablitsy,
  poiskPo: (r: T) => readonly string[]): { stroki: T[]; gruppy: GruppaFiltra[]; vsego: number }
export function variantySortirovki<T>(kolonki: readonly OpisanieKolonki<T>[]): { kod: string; podpis: string }[]
```
Правила: пустое (текст — `null`/пробелы; число — `null`, `''`, неразбираемое) — в конце в обе
стороны; «по убыванию» — обращённым компаратором, не `reverse()`; подписи по умолчанию «от А до Я /
от Я до А» и «по возрастанию / по убыванию».

**`domain/adres.ts`** — `PARAM_POISKA = 'search'`, `PARAM_SORTIROVKI = 'sort'`;
`kodSortirovki`, `razobratKodSortirovki`, `prochitatAdres(p, kolonki)`, `zapisatAdres(prezhnie, s, kolonki)`
(правила — решение 4; неизвестные колонки/коды отбрасываются; ключ колонки `search`/`sort` —
исключение).

**Коды готовых условий:** ингредиенты `price`, `ves`, `card` — `est`/`net`; блюда `price`,
`warnings` — `est`/`net`, `uc` — `vyshe` (через `dorozhe`). Цена `'0.00'` — «есть» (прочерк ≠ ноль).

**`ui/DataTable.tsx`:** `Column<T> = OpisanieKolonki<T> & { align?; priority; render }`; новые
необязательные `sortirovka?: Sortirovka | null` (ставит `aria-sort`) и `zagolovok?: (k) => ReactNode`
(содержимое `th`). Прежние вызовы компилируются без правок.

**`ui/useTablitsaVAdrese.ts`** — `sostoyanie` (поиск — живой ввод), `vvod`, `zadatVvod`,
`pereklyuchitSortirovku`, `zadatSortirovku`, `zadatVybor`, `sbrositVsyo` (поиск и фильтры, не
сортировку; обнуляет и локальный ввод), `adresSeychas()`. Все записи — через одну `izmenit(fn)` с
ref на свежие параметры (сразу обновляется после записи); поиск — через существующий
`useOtlozhennyiPoisk` (300 мс).

**Компоненты:** `Galochki` (fieldset/legend, чекбоксы «значение · счётчик», «Сбросить» у активной
группы); `raspolozhenie.ts` (`raspolozhit(yakor, vyravnivanie, shirinaOkna, prokrutka)`, ширина
панели 264, прижим к краям окна); `Vsplyvashka` (портал, `role="dialog"`, фокус, Escape, Tab по
кругу, щелчок снаружи, якорь «снаружи» не считается); `ZagolovokKolonki` (кнопка сортировки по
`title` + значок фильтра с `aria-expanded`, `aria-haspopup="dialog"`, «Фильтр: Категория, выбрано
2»); `PanelTablitsy` (поиск «Поиск по названию или id», «Найдено: N из M» в `aria-live`, на узком —
выбор «Сортировка» и «Фильтры (k)», фишки, «Сбросить всё»); `Fishki`; `TablitsaSFiltrami` (хук +
`primenit` в `useMemo` + панель + `DataTable`; `onOpen(r, adresSpiska)`); `Icons`: `SortirovkaIcon`,
`VoronkaIcon`, `KrestikIcon`.

**Запросы:** `LIMIT_SPRAVOCHNIKA = 500`, `KLYUCH_INGREDIENTOV = ['ingredients', {limit: 500}]`,
`KLYUCH_BLYUD = ['dishes']`; `useIngredients()` → `GET /ingredients?limit=500`; `useDishes()` →
`GET /dishes`.

---

## 5. Риски

- **Пустая строка и `null`:** текстовые поля по API не `null`; `''` — «(пусто)» в фильтре и «пустое» в
  сортировке; числовой `null` — в конце и условие «нет».
- **Липкая шапка, вероятно, уже не липнет** (`overflow-x: auto` у обёртки делает её контейнером
  прокрутки). Портал корректен при любом исходе; проверить вживую, чинить — отдельным решением.
- **Всплытие событий из портала** идёт по дереву React в `th` — там не должно быть обработчиков.
- **Общий кэш со сверкой:** сверка получит до 500 строк вместо 200 — только лучше.
- **Тесты с глобальным `getByText`:** закрытые панели не рендерятся; в новых тестах искать внутри
  строки (`within`).

## 6. Проверка

1. Локально из `frontend/`: `npm run types && npm run lint && npm run test && npm run build` — больше
   134 тестов, 0 упавших; мутации каждой задачи краснят свои тесты.
2. CI на PR: 8 из 8, время задачи «фронтенд» в пределах бюджета.
3. После слияния и `./scripts/deploy.sh` (по слову Александра): `/healthz` с хешем `main`; вместе с
   Александром на компьютере и телефоне (360 px):
   - `/dishes`: маржа по возрастанию — отрицательные сверху, блюда без цены внизу; по убыванию
     прочерки тоже внизу; «Статус» с настоящими значениями; «выше цены меню» оставляет только красные
     строки; на телефоне «Сортировка» и «Фильтры»;
   - панель у крайней правой колонки не обрезана; Escape и щелчок снаружи закрывают;
   - при наборе в поиске нет запросов к серверу; ссылка с фильтрами в другом окне даёт тот же вид;
     «← Блюда» возвращает вид; фильтр, убравший всё, оставляет шапку;
   - `/ingredients`: предупреждения об обрезке нет (130 < 500), «Ед.» на месте, архивные помечены;
     `/reconciliation` работает;
   - после очередной синхронизации вид экрана сохраняется.
4. Строка доказательств — в ROADMAP.
