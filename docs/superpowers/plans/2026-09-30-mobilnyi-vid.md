# Этап 6: мобильный вид — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На телефоне (уже 1080 px) сайт получает липкую шапку с названием раздела, шторку слева и вкладки внизу вместо меню в потоке, компактные строки списков без «Подробнее», сортировку и фильтры панелью снизу, состав блюда списком и манифест приложения; компьютер не меняется.

**Architecture:** Один порог ширины — `useWide()` (1080 px); в JS ветки по нему, в CSS — по классу `obolochka--telefon`, который ставит `Layout`. Своя модальная панель `ModalnayaPanel` (портал, затемнение, `inert`, ловушка Tab, Escape, возврат фокуса) в двух положениях — слева (шторка) и снизу (панель отбора). Узкие ветки `DataTable`, `PanelTablitsy` и `Sostav` заменяются новыми компонентами; широкие ветки не трогаются. Файлы этапа 5 (`shell/Nav.tsx`, `shell/razdely.ts`, `App.tsx`, `api/*`) не изменяются.

**Tech Stack:** React 18, TypeScript, react-router-dom 6, TanStack Query 5 (без изменений), свой CSS с токенами из `styles/tokens.css`; тесты — Vitest + Testing Library + msw; снимки — Playwright из `backend/.venv`.

**Spec:** `docs/superpowers/specs/2026-09-30-mobilnyi-vid-design.md`

## Global Constraints

- Компьютер (от 1080 px) не меняется; единственное общее изменение — фон красной строки при наведении.
- Не трогать: `frontend/src/shell/Nav.tsx`, `shell/razdely.ts`, `src/App.tsx`, `src/api/*`, `pages/kartochka/*`, `pages/NovyiIngredient.tsx`, `domain/foto.ts`, `ui/FotoVybor.tsx`, `tests/setup.ts`, `package.json`, `package-lock.json`, весь `backend/`, `infra/`, `scripts/`, `CLAUDE.md`, `.claude/agents/*`. В `tests/obolochka.test.tsx` менять только тесты узкого экрана и добавлять новые.
- Новых зависимостей нет. Иконки — только SVG в `ui/Icons.tsx` (контур, `currentColor`, толщина 2). Эмодзи запрещены.
- Числа — строками через `Num`; никаких `Number()`, `parseFloat`, арифметики над деньгами.
- Порог ширины — только `useWide`/`WIDE` из `ui/useWide.ts`; в CSS — класс `obolochka--telefon`, без `@media (min-width: 1080px)`. `@media (pointer: coarse)` и `prefers-reduced-motion` разрешены.
- Доступность: у панелей `role="dialog"`, `aria-modal="true"`, имя; фокус внутри, Tab по кругу, Escape; у кнопки меню `aria-expanded`/`aria-controls`; у вкладок `<nav aria-label>` и `aria-current`; у раскрываемой строки `aria-expanded`.
- Тексты для людей — по-русски; имена в коде фронтенда — транслит (как в проекте); комментарии — по-русски, объясняют «почему».
- Каждая задача: тесты сначала красные, потом код, потом зелёные; `cd frontend && npm run types && npm run lint && npm run test` перед каждым коммитом задачи; `npm run build` — перед PR.
- Коммиты завершаются строкой `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Файлы создавать и править инструментами Write/Edit (Windows), после правок проверять `git ls-files --eol <файл>` → `w/lf`.
- Агенты-исполнители и ревьюеры — на модели текущего чата (Fable 5.1, `model: "fable"`).
- Ветка `mobilnyi-vid`, рабочее дерево `C:\Users\karppacho\Desktop\projects\Artem\kitchen_platform-mobile`. Три PR: задачи 1–8 (оболочка), 9–13 (строки и отбор), 14–16 (карточка). Слияние и выкладка — только по слову Александра.

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `src/shell/razdelPoAdresu.ts` | чистая функция: раздел по `pathname` |
| `src/ui/ModalnayaPanel.tsx`, `src/ui/modalnayaPanel.css` | модальная панель слева/снизу: портал, затемнение, `inert`, запрет прокрутки, фокус, Escape |
| `src/shell/Granitsa.tsx` | граница ошибок вокруг экрана |
| `src/shell/Vkladki.tsx` | вкладки внизу из `RAZDELY` без `faza`; прячутся при вводе |
| `src/shell/Shtorka.tsx` | содержимое шторки: `Nav`, имя, роли, «Выйти» |
| `src/shell/Layout.tsx`, `src/shell/shell.css` | оболочка: класс телефона, шапка, шторка, вкладки, граница |
| `index.html`, `public/manifest.webmanifest`, `public/icons/*`, `public/favicon.svg`, `public/apple-touch-icon.png` | приложение на главном экране |
| `e2e/snimki.py`, `e2e/README.md` | снимки и проверки на 360/390/1440 |
| `src/ui/Icons.tsx` | + `ShevronIcon` |
| `src/ui/DataTable.tsx`, `src/ui/StrokaSpiska.tsx`, `src/ui/table.css` | `Column.uzkiy`; строка телефона |
| `src/domain/slova.ts` | «N замечаний» — склонение |
| `src/pages/Dishes.tsx`, `src/pages/Ingredients.tsx` | роли колонок на телефоне |
| `src/ui/PanelOtbora.tsx`, `src/ui/PanelTablitsy.tsx`, `src/ui/panelTablitsy.css` | панель отбора снизу |
| `src/pages/DishDetail.tsx`, `src/pages/pages.css`, `src/auth/auth.css` | показатели 2×2, состав списком, цели 44 px, поля 16 px |

---

## PR-1 «Оболочка телефона» — задачи 1–8

### Task 1: Раздел по адресу

**Files:**
- Create: `frontend/src/shell/razdelPoAdresu.ts`
- Test: `frontend/tests/razdelPoAdresu.test.ts`

**Interfaces:**
- Produces: `razdelPoAdresu(pathname: string, razdely: readonly Razdel[]): Razdel | null` — раздел, чей `put` равен пути или является его префиксом до `/`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/tests/razdelPoAdresu.test.ts
import { expect, test } from 'vitest'

import { RAZDELY } from '../src/shell/razdely'
import { razdelPoAdresu } from '../src/shell/razdelPoAdresu'

test('точный путь раздела', () => {
  expect(razdelPoAdresu('/dishes', RAZDELY)?.nazvanie).toBe('Блюда')
})

test('вложенный путь — карточка блюда — тоже «Блюда»', () => {
  expect(razdelPoAdresu('/dishes/B001', RAZDELY)?.nazvanie).toBe('Блюда')
})

test('похожий префикс без слэша — не раздел', () => {
  // «/dishesX» не должен считаться «Блюдами».
  expect(razdelPoAdresu('/dishesX', RAZDELY)).toBeNull()
})

test('неизвестный адрес — null', () => {
  expect(razdelPoAdresu('/net-takogo', RAZDELY)).toBeNull()
  expect(razdelPoAdresu('/', RAZDELY)).toBeNull()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/razdelPoAdresu.test.ts`
Expected: FAIL — модуль `../src/shell/razdelPoAdresu` не найден.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/shell/razdelPoAdresu.ts
import type { Razdel } from './razdely'

/**
 * Раздел, которому принадлежит адрес: `/dishes/B001` — карточка блюда, и в
 * шапке телефона стоит «Блюда». Префикс считается только до `/`, чтобы
 * `/dishesX` не оказался «Блюдами». Ничего не нашлось — null: шапка
 * покажет «Кухня».
 */
export function razdelPoAdresu(pathname: string, razdely: readonly Razdel[]): Razdel | null {
  return razdely.find((r) => pathname === r.put || pathname.startsWith(`${r.put}/`)) ?? null
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run tests/razdelPoAdresu.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shell/razdelPoAdresu.ts frontend/tests/razdelPoAdresu.test.ts
git commit -m "Оболочка: раздел по адресу — название для шапки телефона"
```

---

### Task 2: Модальная панель слева и снизу

**Files:**
- Create: `frontend/src/ui/ModalnayaPanel.tsx`, `frontend/src/ui/modalnayaPanel.css`
- Test: `frontend/tests/modalnayaPanel.test.tsx`

**Interfaces:**
- Consumes: `KrestikIcon` из `src/ui/Icons.tsx` (есть).
- Produces:
  ```ts
  type Props = {
    id: string
    storona: 'sleva' | 'snizu'
    nazvanie: string            // имя диалога (aria-label)
    zagolovok?: string          // видимый заголовок; по умолчанию nazvanie
    onZakryt: () => void
    otkryvatel: RefObject<HTMLElement>   // кнопка, открывшая панель
    children: ReactNode
    niz?: ReactNode             // прилипший низ панели
  }
  export function ModalnayaPanel(props: Props): JSX.Element
  ```

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/tests/modalnayaPanel.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { expect, test } from 'vitest'

import { ModalnayaPanel } from '../src/ui/ModalnayaPanel'

function Stend({ storona = 'sleva' as const }) {
  const knopka = useRef<HTMLButtonElement>(null)
  const [otkryta, otkryt] = useState(false)
  return (
    <div>
      <button ref={knopka} type="button" onClick={() => otkryt(true)}>
        Открыть
      </button>
      <button type="button">Снаружи</button>
      {otkryta && (
        <ModalnayaPanel
          id="panel"
          storona={storona}
          nazvanie="Разделы"
          zagolovok="Кухня"
          onZakryt={() => otkryt(false)}
          otkryvatel={knopka}
          niz={<button type="button">Показать 5</button>}
        >
          <a href="/a">Первая</a>
          <a href="/b">Вторая</a>
        </ModalnayaPanel>
      )}
    </div>
  )
}

test('открытая панель — диалог с именем, заголовком и кнопкой «Закрыть»', async () => {
  render(<Stend />)
  await userEvent.click(screen.getByRole('button', { name: 'Открыть' }))

  const dialog = screen.getByRole('dialog', { name: 'Разделы' })
  expect(dialog).toHaveAttribute('aria-modal', 'true')
  expect(dialog).toHaveAttribute('id', 'panel')
  expect(dialog).toHaveTextContent('Кухня')
  expect(screen.getByRole('button', { name: 'Закрыть' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Показать 5' })).toBeInTheDocument()
})

test('фокус входит в панель, Tab ходит по кругу, Escape закрывает и возвращает фокус', async () => {
  render(<Stend />)
  const otkryt = screen.getByRole('button', { name: 'Открыть' })
  await userEvent.click(otkryt)

  // Первый табуемый — «Закрыть» в шапке панели.
  expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveFocus()
  await userEvent.tab()
  expect(screen.getByRole('link', { name: 'Первая' })).toHaveFocus()
  await userEvent.tab()
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Показать 5' })).toHaveFocus()
  // За последним — снова первый, а не «Снаружи».
  await userEvent.tab()
  expect(screen.getByRole('button', { name: 'Закрыть' })).toHaveFocus()
  await userEvent.tab({ shift: true })
  expect(screen.getByRole('button', { name: 'Показать 5' })).toHaveFocus()

  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(otkryt).toHaveFocus()
})

test('остальная страница под панелью — inert, при закрытии снимается', async () => {
  const { container } = render(<Stend />)
  await userEvent.click(screen.getByRole('button', { name: 'Открыть' }))
  // Контейнер Testing Library — дитя body рядом с порталом панели.
  expect(container).toHaveAttribute('inert')
  expect(document.body.style.overflow).toBe('hidden')

  await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }))
  expect(container).not.toHaveAttribute('inert')
  expect(document.body.style.overflow).toBe('')
})

test('нажатие на затемнение закрывает панель', async () => {
  const { baseElement } = render(<Stend />)
  await userEvent.click(screen.getByRole('button', { name: 'Открыть' }))
  const fon = baseElement.querySelector('.modalnaya-fon')!
  await userEvent.pointer({ keys: '[MouseLeft>]', target: fon })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('положение — классом: слева или снизу', async () => {
  const { baseElement, unmount } = render(<Stend storona="snizu" />)
  await userEvent.click(screen.getByRole('button', { name: 'Открыть' }))
  expect(baseElement.querySelector('.modalnaya')).toHaveClass('modalnaya--snizu')
  unmount()

  render(<Stend storona="sleva" />)
  await userEvent.click(screen.getByRole('button', { name: 'Открыть' }))
  expect(document.querySelector('.modalnaya')).toHaveClass('modalnaya--sleva')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/modalnayaPanel.test.tsx`
Expected: FAIL — модуль `../src/ui/ModalnayaPanel` не найден.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/src/ui/ModalnayaPanel.tsx
import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import { KrestikIcon } from './Icons'
import './modalnayaPanel.css'

type Props = {
  /** id диалога — на него указывает `aria-controls` открывшей кнопки. */
  id: string
  /** Откуда выезжает: шторка разделов — слева, панель отбора — снизу. */
  storona: 'sleva' | 'snizu'
  /** Имя диалога для программы чтения с экрана. */
  nazvanie: string
  /** Видимый заголовок в шапке панели; по умолчанию — `nazvanie`. */
  zagolovok?: string
  onZakryt: () => void
  /** Кнопка, открывшая панель: на неё возвращается фокус при закрытии. */
  otkryvatel: RefObject<HTMLElement>
  children: ReactNode
  /** Прилипший низ панели — кнопки «Показать N», «Сбросить всё». */
  niz?: ReactNode
}

// Куда встаёт фокус по Tab (как у Vsplyvashka).
const TABUEMYE = [
  'button:not(:disabled)',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

/**
 * Модальная панель поверх страницы — портал в `document.body`: шторка
 * разделов и панель сортировки и фильтров на телефоне. Не `<dialog>`: в
 * jsdom 25 нет `showModal`, а тесты — на нём (та же причина, что у
 * `Vsplyvashka`).
 *
 * Пока панель открыта: всё остальное в `body` — `inert` (клавиатура и
 * программа чтения с экрана не уходят под затемнение), страница не
 * прокручивается (на iOS `overflow: hidden` у body не действует — поэтому
 * `position: fixed` с возвратом прокрутки при закрытии), фокус — внутри,
 * Tab по кругу, Escape закрывает, при закрытии фокус — на открывшую кнопку.
 *
 * Гасить click после нажатия на затемнение, как делает `Vsplyvashka`, не
 * нужно: затемнение накрывает всю страницу, и нажатие приходится на него,
 * а не на строку таблицы под ним.
 */
export function ModalnayaPanel({ id, storona, nazvanie, zagolovok, onZakryt, otkryvatel, children, niz }: Props) {
  const koren = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  // inert на соседях по body и запрет прокрутки — на время жизни панели.
  useLayoutEffect(() => {
    const svoy = koren.current
    const sosedi = [...document.body.children].filter((el) => el !== svoy && !el.hasAttribute('inert'))
    for (const el of sosedi) el.setAttribute('inert', '')

    const body = document.body
    const prokrutka = window.scrollY
    const bylo = { position: body.style.position, top: body.style.top, width: body.style.width, overflow: body.style.overflow }
    body.style.position = 'fixed'
    body.style.top = `-${prokrutka}px`
    body.style.width = '100%'
    body.style.overflow = 'hidden'
    return () => {
      for (const el of sosedi) el.removeAttribute('inert')
      body.style.position = bylo.position
      body.style.top = bylo.top
      body.style.width = bylo.width
      body.style.overflow = bylo.overflow
      // В jsdom scrollTo не реализован; при нулевой прокрутке возвращать нечего.
      if (prokrutka) window.scrollTo(0, prokrutka)
    }
  }, [])

  // Фокус — на первый табуемый элемент (кнопку «Закрыть»), иначе на панель.
  useLayoutEffect(() => {
    const pervyi = panel.current?.querySelector<HTMLElement>(TABUEMYE)
    ;(pervyi ?? panel.current)?.focus()
  }, [])

  // Закрылась с фокусом внутри — фокус на открывшую кнопку, а не на body.
  // Очистка идёт до того, как панель уберут из документа, — фокус ещё внутри.
  useLayoutEffect(() => {
    const svoy = koren.current
    const knopka = otkryvatel.current
    return () => {
      if (svoy?.contains(document.activeElement)) knopka?.focus()
    }
  }, [otkryvatel])

  function klavisha(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onZakryt()
      return
    }
    if (event.key !== 'Tab' || panel.current === null) return
    const spisok = [...panel.current.querySelectorAll<HTMLElement>(TABUEMYE)]
    const pervyi = spisok[0]
    const posledniy = spisok[spisok.length - 1]
    if (pervyi === undefined || posledniy === undefined) return
    const seychas = document.activeElement
    const vKruge = spisok.some((el) => el === seychas)
    if (event.shiftKey && (!vKruge || seychas === pervyi)) {
      event.preventDefault()
      posledniy.focus()
    } else if (!event.shiftKey && (!vKruge || seychas === posledniy)) {
      event.preventDefault()
      pervyi.focus()
    }
  }

  return createPortal(
    <div ref={koren} className={`modalnaya modalnaya--${storona}`}>
      {/* Нажатие, а не click: закрываем в момент касания, как Vsplyvashka. */}
      <div className="modalnaya-fon" onPointerDown={onZakryt} />
      <div
        ref={panel}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-label={nazvanie}
        tabIndex={-1}
        className="modalnaya-panel"
        onKeyDown={klavisha}
      >
        <div className="modalnaya-shapka">
          <span className="modalnaya-zagolovok">{zagolovok ?? nazvanie}</span>
          <button type="button" className="modalnaya-zakryt" aria-label="Закрыть" onClick={onZakryt}>
            <KrestikIcon />
          </button>
        </div>
        <div className="modalnaya-soderzhimoe">{children}</div>
        {niz !== undefined && <div className="modalnaya-niz">{niz}</div>}
      </div>
    </div>,
    document.body,
  )
}
```

```css
/* frontend/src/ui/modalnayaPanel.css */
/* Модальная панель поверх страницы: шторка разделов слева, панель отбора
   снизу. Слой выше липкой шапки и вкладок (z-index 2 в shell.css). */
.modalnaya {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: flex;
}

.modalnaya-fon {
  position: absolute;
  inset: 0;
  background: color-mix(in srgb, var(--tekst) 40%, transparent);
}

.modalnaya-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--poverhnost);
  box-shadow: 0 0 24px color-mix(in srgb, var(--tekst) 25%, transparent);
  outline: none;
}

/* Слева: во всю высоту, не шире 85 % экрана, чтобы затемнение оставалось
   местом «нажать мимо». */
.modalnaya--sleva .modalnaya-panel {
  width: min(280px, 85vw);
  height: 100%;
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
  animation: modalnaya-sleva 200ms ease-out;
}

/* Снизу: во всю ширину, до 85 % высоты, скруглённый верх. */
.modalnaya--snizu {
  align-items: flex-end;
}

.modalnaya--snizu .modalnaya-panel {
  width: 100%;
  max-height: 85dvh;
  border-radius: calc(var(--radius) * 4) calc(var(--radius) * 4) 0 0;
  padding-bottom: env(safe-area-inset-bottom);
  animation: modalnaya-snizu 200ms ease-out;
}

.modalnaya-shapka {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: calc(var(--shag) * 2);
  padding: calc(var(--shag) * 2) calc(var(--shag) * 2) calc(var(--shag) * 2) calc(var(--shag) * 4);
  border-bottom: 1px solid var(--granitsa);
}

.modalnaya-zagolovok {
  font-weight: 600;
  font-size: 16px;
}

.modalnaya-zakryt {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  border: 0;
  border-radius: var(--radius);
  background: none;
  color: inherit;
  cursor: pointer;
}

.modalnaya-zakryt svg {
  width: 20px;
  height: 20px;
}

.modalnaya-soderzhimoe {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: calc(var(--shag) * 3) calc(var(--shag) * 4);
}

.modalnaya-niz {
  display: flex;
  gap: calc(var(--shag) * 2);
  align-items: center;
  padding: calc(var(--shag) * 3) calc(var(--shag) * 4);
  border-top: 1px solid var(--granitsa);
  background: var(--poverhnost);
}

@keyframes modalnaya-sleva {
  from { transform: translateX(-100%); }
  to { transform: translateX(0); }
}

@keyframes modalnaya-snizu {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .modalnaya-panel {
    animation: none;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run tests/modalnayaPanel.test.tsx`
Expected: PASS (5 tests). Если тест затемнения не проходит из-за `pointer` в user-event, заменить на `fireEvent.pointerDown(fon)` — компонент слушает `pointerdown`.

- [ ] **Step 5: Run the whole suite and commit**

Run: `cd frontend && npm run types && npm run lint && npm run test`
Expected: всё зелёное (старые 333 + 5 + 4).

```bash
git add frontend/src/ui/ModalnayaPanel.tsx frontend/src/ui/modalnayaPanel.css frontend/tests/modalnayaPanel.test.tsx
git commit -m "Модальная панель слева и снизу: затемнение, inert, фокус по кругу, Escape"
```

---

### Task 3: Граница ошибок экрана

**Files:**
- Create: `frontend/src/shell/Granitsa.tsx`
- Test: `frontend/tests/granitsa.test.tsx`
- Modify: `frontend/src/shell/shell.css` (стили `.granitsa` — добавить в конец файла; полная перестройка файла — в задаче 5)

**Interfaces:**
- Produces: `export class Granitsa extends Component<{ children: ReactNode }>` — при исключении дочернего дерева рисует `role="alert"` с заголовком «Экран не открылся» и кнопкой «Перезагрузить».

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/granitsa.test.tsx
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { Granitsa } from '../src/shell/Granitsa'

function Lomaetsya(): never {
  throw new Error('нарочно')
}

beforeEach(() => {
  // React и граница пишут ошибку в консоль — в тесте это ожидаемо.
  vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => vi.restoreAllMocks())

test('исключение экрана даёт сообщение и «Перезагрузить», а не пустоту', () => {
  render(
    <Granitsa>
      <Lomaetsya />
    </Granitsa>,
  )
  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('Экран не открылся')
  expect(screen.getByRole('button', { name: 'Перезагрузить' })).toBeInTheDocument()
})

test('без исключения дети рисуются как есть', () => {
  render(
    <Granitsa>
      <p>Всё в порядке</p>
    </Granitsa>,
  )
  expect(screen.getByText('Всё в порядке')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/granitsa.test.tsx`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/src/shell/Granitsa.tsx
import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { slomano: boolean }

/**
 * Граница ошибок вокруг экрана раздела. Без неё любое исключение при
 * отрисовке (неожиданный ответ, ошибка в коде экрана) даёт белый лист на
 * весь сайт — без шапки, меню и «Выйти». С ней ломается только экран, а
 * оболочка живёт. Классовый компонент: границы ошибок в React 18 иначе не
 * пишутся. Сбрасывается сменой `key` (Layout ставит `pathname`).
 */
export class Granitsa extends Component<Props, State> {
  state: State = { slomano: false }

  static getDerivedStateFromError(): State {
    return { slomano: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Экран не отрисовался', error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.slomano) return this.props.children
    return (
      <div className="granitsa" role="alert">
        <h1>Экран не открылся</h1>
        <p>Что-то сломалось при показе этого экрана. Перезагрузите страницу; если повторится — сообщите разработчику.</p>
        <button type="button" onClick={() => window.location.reload()}>
          Перезагрузить
        </button>
      </div>
    )
  }
}
```

Добавить в конец `frontend/src/shell/shell.css`:

```css
/* Экран раздела упал — сообщение на месте экрана, оболочка живёт. */
.granitsa {
  padding: calc(var(--shag) * 6) 0;
}

.granitsa h1 {
  margin: 0 0 calc(var(--shag) * 2);
  font-size: 18px;
}

.granitsa p {
  margin: 0 0 calc(var(--shag) * 3);
  color: var(--priglushyonnyy);
}

.granitsa button {
  min-height: 44px;
  padding: var(--shag) calc(var(--shag) * 4);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: var(--poverhnost);
  font: inherit;
  cursor: pointer;
}
```

- [ ] **Step 4: Run tests, then commit**

Run: `cd frontend && npx vitest run tests/granitsa.test.tsx && npm run types && npm run lint`
Expected: PASS (2 tests), типы и линт зелёные.

```bash
git add frontend/src/shell/Granitsa.tsx frontend/src/shell/shell.css frontend/tests/granitsa.test.tsx
git commit -m "Граница ошибок экрана: «Перезагрузить» вместо белого листа"
```

---

### Task 4: Вкладки внизу

**Files:**
- Create: `frontend/src/shell/Vkladki.tsx`
- Test: `frontend/tests/vkladki.test.tsx`

**Interfaces:**
- Consumes: `RAZDELY`, тип `Razdel` из `src/shell/razdely.ts` (не менять); `NavLink` из react-router-dom.
- Produces: `razdelyVkladok(razdely?: readonly Razdel[]): Razdel[]` (живые разделы: без `faza`) и `export function Vkladki(): JSX.Element` — `<nav class="vkladki" aria-label="Основные разделы">` со ссылками; при фокусе в поле ввода получает атрибут `hidden`.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/tests/vkladki.test.tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'

import { RAZDELY } from '../src/shell/razdely'
import { razdelyVkladok, Vkladki } from '../src/shell/Vkladki'

function narisovat(putj = '/dishes') {
  return render(
    <MemoryRouter initialEntries={[putj]}>
      <main>
        <input aria-label="Поиск" />
        <button type="button">Кнопка</button>
      </main>
      <Vkladki />
    </MemoryRouter>,
  )
}

test('вкладки — живые разделы из того же списка, без «скоро»', () => {
  const zhivye = RAZDELY.filter((r) => !r.faza)
  expect(razdelyVkladok()).toEqual(zhivye)
  expect(zhivye.map((r) => r.nazvanie)).toEqual(['Справочник', 'Блюда', 'Сверка справочника'])
  // Функция принимает и чужой список: этап 5 подставит отфильтрованный по роли.
  expect(razdelyVkladok([{ put: '/x', nazvanie: 'Икс' }, { put: '/y', nazvanie: 'Игрек', faza: 'фаза 9' }])).toEqual([
    { put: '/x', nazvanie: 'Икс' },
  ])
})

test('текущая вкладка помечена aria-current, длинное имя укорочено, но доступное имя полное', () => {
  narisovat('/reconciliation')
  const nav = screen.getByRole('navigation', { name: 'Основные разделы' })
  const sverka = within(nav).getByRole('link', { name: 'Сверка справочника' })
  expect(sverka).toHaveAttribute('aria-current', 'page')
  expect(sverka).toHaveTextContent('Сверка')
  expect(within(nav).getByRole('link', { name: 'Блюда' })).not.toHaveAttribute('aria-current')
})

test('вложенный адрес держит вкладку раздела текущей', () => {
  narisovat('/dishes/B001')
  const nav = screen.getByRole('navigation', { name: 'Основные разделы' })
  expect(within(nav).getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
})

test('пока фокус в поле ввода, вкладки спрятаны; ушёл — вернулись', async () => {
  narisovat()
  const nav = screen.getByRole('navigation', { name: 'Основные разделы' })
  await userEvent.click(screen.getByRole('textbox', { name: 'Поиск' }))
  expect(nav).toHaveAttribute('hidden')
  await userEvent.click(screen.getByRole('button', { name: 'Кнопка' }))
  expect(nav).not.toHaveAttribute('hidden')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/vkladki.test.tsx`
Expected: FAIL — модуль не найден.

- [ ] **Step 3: Write the implementation**

```tsx
// frontend/src/shell/Vkladki.tsx
import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'

import { RAZDELY, type Razdel } from './razdely'

/**
 * Разделы, живущие вкладками внизу телефона: живые, без пометки «скоро».
 * Список тот же, что у меню (`RAZDELY`), своей копии нет: появится живой
 * раздел — появится вкладка. Этап 5 добавит разделам роли и подставит сюда
 * отфильтрованный по роли список — у повара вкладок не будет.
 */
export function razdelyVkladok(razdely: readonly Razdel[] = RAZDELY): Razdel[] {
  return razdely.filter((r) => !r.faza)
}

// Подписи вкладок короче названий разделов: три вкладки делят 360 px.
// Доступное имя остаётся полным — как у пункта меню.
const KOROTKO: Record<string, string> = { '/reconciliation': 'Сверка' }

// Поля, при фокусе в которых вкладки прячутся: iOS держит закреплённые
// панели над клавиатурой, и вкладки закрывали бы половину экрана.
const POLYA = 'input, textarea, select'

export function Vkladki() {
  const [spryatany, spryatat] = useState(false)

  useEffect(() => {
    function vPole(event: FocusEvent) {
      spryatat(event.target instanceof Element && event.target.matches(POLYA))
    }
    function izPolya() {
      spryatat(false)
    }
    document.addEventListener('focusin', vPole)
    document.addEventListener('focusout', izPolya)
    return () => {
      document.removeEventListener('focusin', vPole)
      document.removeEventListener('focusout', izPolya)
    }
  }, [])

  return (
    <nav className="vkladki" aria-label="Основные разделы" hidden={spryatany}>
      {razdelyVkladok().map((r) => {
        const korotko = KOROTKO[r.put]
        return (
          <NavLink
            key={r.put}
            to={r.put}
            aria-label={korotko ? r.nazvanie : undefined}
            className={({ isActive }) => (isActive ? 'vkladka vkladka--tekushchaya' : 'vkladka')}
          >
            {korotko ?? r.nazvanie}
          </NavLink>
        )
      })}
    </nav>
  )
}
```

- [ ] **Step 4: Run tests, then commit**

Run: `cd frontend && npx vitest run tests/vkladki.test.tsx && npm run types && npm run lint`
Expected: PASS (4 tests). Примечание: `focusout` срабатывает раньше `focusin` следующего элемента — порядок событий даёт верное итоговое состояние.

```bash
git add frontend/src/shell/Vkladki.tsx frontend/tests/vkladki.test.tsx
git commit -m "Вкладки внизу телефона: живые разделы из RAZDELY, прячутся при вводе"
```

---
### Task 5: Шторка и оболочка телефона

**Files:**
- Create: `frontend/src/shell/Shtorka.tsx`
- Modify: `frontend/src/shell/Layout.tsx` (целиком), `frontend/src/shell/shell.css` (целиком, кроме блока `.granitsa` из задачи 3)
- Test: `frontend/tests/obolochka.test.tsx` (заменить два теста узкого экрана, добавить новые)

**Interfaces:**
- Consumes: `ModalnayaPanel` (задача 2), `Granitsa` (задача 3), `Vkladki` (задача 4), `razdelPoAdresu` (задача 1), `Nav` и `RAZDELY` (без изменений), `useSession`/`ROLI` из `auth/session.tsx`, `useWide` из `ui/useWide.ts`, `MenuIcon`.
- Produces: `Shtorka({ otkryvatel, onZakryt })`; `Layout` с классом `obolochka obolochka--telefon` на телефоне; `export const ID_SHTORKI = 'shtorka-razdelov'`.

- [ ] **Step 1: Replace the two narrow-screen tests and add new ones in `tests/obolochka.test.tsx`**

Удалить тесты «на узком экране меню открывается кнопкой и закрывается после выбора пункта» и «шапка показывает обе роли по-русски, включая узкий экран». Добавить в конец файла (обработчик `/api/auth/logout` — для теста выхода):

```tsx
test('на телефоне меню — шторка поверх страницы: открывается кнопкой, закрывается по пункту', async () => {
  setViewport(360)
  narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })

  const knopka = screen.getByRole('button', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  await userEvent.click(knopka)
  const shtorka = screen.getByRole('dialog', { name: 'Разделы' })
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(knopka).toHaveAttribute('aria-controls', shtorka.id)
  // Все восемь разделов — внутри шторки, тем же Nav.
  for (const razdel of RAZDELY) {
    expect(within(shtorka).getByRole('link', { name: razdel.nazvanie })).toBeInTheDocument()
  }

  await userEvent.click(within(shtorka).getByRole('link', { name: 'Справочник' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(await screen.findByRole('heading', { name: 'Справочник ингредиентов' })).toBeInTheDocument()
})

test('на телефоне шторка закрывается по Escape и по нажатию на затемнение', async () => {
  setViewport(360)
  const { baseElement } = narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })
  const knopka = screen.getByRole('button', { name: 'Разделы' })

  await userEvent.click(knopka)
  expect(screen.getByRole('dialog', { name: 'Разделы' })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka).toHaveFocus()

  await userEvent.click(knopka)
  fireEvent.pointerDown(baseElement.querySelector('.modalnaya-fon')!)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('на телефоне имя, роли и «Выйти» — в шторке, а в шапке — название раздела', async () => {
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef', 'developer'] }),
    ),
    http.post('/api/auth/logout', () => new HttpResponse(null, { status: 204 })),
  )
  setViewport(360)
  narisovat('/dishes/B001')
  await screen.findByRole('heading', { name: 'Тестовое блюдо' })

  const shapka = screen.getByRole('banner')
  expect(shapka).toHaveTextContent('Блюда')
  expect(within(shapka).queryByRole('button', { name: 'Выйти' })).not.toBeInTheDocument()
  expect(within(shapka).queryByText('Алексей')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Разделы' }))
  const shtorka = screen.getByRole('dialog', { name: 'Разделы' })
  expect(within(shtorka).getByText('Алексей')).toBeInTheDocument()
  expect(within(shtorka).getByText('бренд-шеф, разработчик')).toBeInTheDocument()

  await userEvent.click(within(shtorka).getByRole('button', { name: 'Выйти' }))
  expect(await screen.findByRole('heading', { name: 'Кухня' })).toBeInTheDocument()
})

test('на телефоне внизу вкладки живых разделов, на компьютере их нет', async () => {
  setViewport(360)
  const { unmount } = narisovat()
  await screen.findByRole('heading', { name: 'Блюда' })
  const vkladki = screen.getByRole('navigation', { name: 'Основные разделы' })
  expect(within(vkladki).getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
  expect(within(vkladki).getAllByRole('link')).toHaveLength(3)
  unmount()

  setViewport(1440)
  narisovat()
  await screen.findByText('Алексей')
  expect(screen.queryByRole('navigation', { name: 'Основные разделы' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
})

test('сломавшийся экран не роняет оболочку', async () => {
  // Ответ карточки, от которого экран падает при отрисовке: components не
  // массив. Граница показывает сообщение, шапка и меню остаются.
  vi.spyOn(console, 'error').mockImplementation(() => {})
  server.use(http.get('/api/dishes/B001', () => HttpResponse.json({ legacy_id: 'B001', name: 'Сломанное', components: null })))
  narisovat('/dishes/B001')
  expect(await screen.findByRole('alert')).toHaveTextContent('Экран не открылся')
  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
  vi.restoreAllMocks()
})
```

Импорты в начале файла дополнить: `fireEvent` из `@testing-library/react`, `vi` из `vitest`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/obolochka.test.tsx`
Expected: новые тесты FAIL (нет `dialog`, нет `navigation`, «Выйти» в шапке на 360).

- [ ] **Step 3: Write `Shtorka.tsx`**

```tsx
// frontend/src/shell/Shtorka.tsx
import type { RefObject } from 'react'

import { ROLI, useSession } from '../auth/session'
import { ModalnayaPanel } from '../ui/ModalnayaPanel'
import { Nav } from './Nav'

export const ID_SHTORKI = 'shtorka-razdelov'

type Props = {
  otkryvatel: RefObject<HTMLButtonElement>
  onZakryt: () => void
}

/**
 * Шторка разделов на телефоне: тот же `Nav`, что в боковой панели
 * компьютера, — своего списка разделов у шторки нет. Внизу — кто вошёл и
 * «Выйти»: в шапке телефона им места нет, а шторка открывается одним
 * нажатием.
 */
export function Shtorka({ otkryvatel, onZakryt }: Props) {
  const { me, logout } = useSession()
  const roli = me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')

  return (
    <ModalnayaPanel
      id={ID_SHTORKI}
      storona="sleva"
      nazvanie="Разделы"
      zagolovok="Кухня"
      onZakryt={onZakryt}
      otkryvatel={otkryvatel}
      niz={
        <div className="shtorka-niz">
          <span className="shtorka-kto">
            <b>{me?.display_name}</b>
            <span className="shtorka-rol">{roli}</span>
          </span>
          <button
            type="button"
            className="shtorka-vyhod"
            onClick={() => {
              onZakryt()
              void logout()
            }}
          >
            Выйти
          </button>
        </div>
      }
    >
      <Nav onGo={onZakryt} />
    </ModalnayaPanel>
  )
}
```

- [ ] **Step 4: Rewrite `Layout.tsx`**

```tsx
// frontend/src/shell/Layout.tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'
import { MenuIcon } from '../ui/Icons'
import { useWide } from '../ui/useWide'
import { Granitsa } from './Granitsa'
import { Nav } from './Nav'
import { razdelPoAdresu } from './razdelPoAdresu'
import { RAZDELY } from './razdely'
import { ID_SHTORKI, Shtorka } from './Shtorka'
import './shell.css'
import { Svezhest } from './Svezhest'
import { Vkladki } from './Vkladki'

/**
 * Оболочка. Компьютер (от 1080 px): шапка с именем и «Выйти», боковая
 * панель с меню. Телефон: липкая шапка с кнопкой меню и названием текущего
 * раздела, шторка слева поверх страницы, вкладки внизу. Порог — `useWide`;
 * CSS ветвится по классу `obolochka--telefon`, а не по своему медиазапросу.
 */
export function Layout() {
  const { me, logout } = useSession()
  const wide = useWide()
  const { pathname } = useLocation()
  const [menyuOtkryto, otkryt] = useState(false)
  const knopkaMenyu = useRef<HTMLButtonElement>(null)
  const zakryt = useCallback(() => otkryt(false), [])

  // Окно стало широким при открытой шторке — шторки на компьютере нет.
  useEffect(() => {
    if (wide) otkryt(false)
  }, [wide])

  const razdel = razdelPoAdresu(pathname, RAZDELY)
  const roli = me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')

  return (
    <div className={wide ? 'obolochka' : 'obolochka obolochka--telefon'}>
      <header className="shapka">
        {!wide && (
          <button
            ref={knopkaMenyu}
            type="button"
            className="shapka-menyu"
            aria-label="Разделы"
            aria-expanded={menyuOtkryto}
            aria-controls={menyuOtkryto ? ID_SHTORKI : undefined}
            onClick={() => otkryt(!menyuOtkryto)}
          >
            <MenuIcon />
          </button>
        )}
        <span className="shapka-nazvanie">{wide ? 'Кухня' : (razdel?.nazvanie ?? 'Кухня')}</span>
        {wide && (
          <>
            <span className="shapka-kto">
              <b>{me?.display_name}</b>
              <span className="shapka-rol">{roli}</span>
            </span>
            <button type="button" className="shapka-vyhod" onClick={() => void logout()}>
              Выйти
            </button>
          </>
        )}
      </header>

      <div className="obolochka-telo">
        {wide && (
          <aside className="bok">
            <Nav />
          </aside>
        )}
        <main className="soderzhimoe">
          <Svezhest />
          {/* Ключ по адресу: упавший экран не тянет ошибку на соседний раздел. */}
          <Granitsa key={pathname}>
            <Outlet />
          </Granitsa>
        </main>
      </div>

      {!wide && menyuOtkryto && <Shtorka otkryvatel={knopkaMenyu} onZakryt={zakryt} />}
      {!wide && <Vkladki />}
    </div>
  )
}
```

- [ ] **Step 5: Rewrite `shell.css`** (блок `.granitsa` из задачи 3 оставить в конце)

```css
/* Оболочка. Ширина ветвится классом obolochka--telefon (ставит Layout по
   useWide), а не медиазапросом: порог живёт в одном месте — ui/useWide.ts. */
.obolochka {
  min-height: 100dvh;
}

.shapka {
  display: flex;
  align-items: center;
  gap: calc(var(--shag) * 2);
  padding: calc(var(--shag) * 2) calc(var(--shag) * 3);
  background: var(--poverhnost);
  border-bottom: 1px solid var(--granitsa);
}

/* На телефоне шапка липнет к верху и отступает под вырез экрана. Слой ниже
   модальной панели (z-index 10 в modalnayaPanel.css). */
.obolochka--telefon .shapka {
  position: sticky;
  top: 0;
  z-index: 2;
  padding-top: calc(var(--shag) * 2 + env(safe-area-inset-top));
}

.shapka-nazvanie {
  font-weight: 600;
}

.obolochka--telefon .shapka-nazvanie {
  font-size: 16px;
}

.shapka-kto {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.2;
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: right;
}

.shapka-rol,
.shtorka-rol {
  color: var(--priglushyonnyy);
  font-size: 12px;
}

.shapka-vyhod,
.shapka-menyu {
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: none;
  color: inherit;
  padding: var(--shag) calc(var(--shag) * 2);
  font: inherit;
  cursor: pointer;
  flex: none;
}

/* Кнопка меню — цель 44 px, но строка шапки не растёт: отрицательное поле
   возвращает место. */
.shapka-menyu {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  margin: calc(var(--shag) * -1) 0;
  padding: 0;
}

.obolochka-telo {
  display: flex;
  flex-direction: row;
  align-items: flex-start;
}

.obolochka--telefon .obolochka-telo {
  flex-direction: column;
  align-items: stretch;
}

.bok {
  width: 220px;
  flex: none;
  padding: calc(var(--shag) * 2);
}

.menyu {
  display: grid;
  gap: 2px;
}

.menyu-punkt {
  display: flex;
  justify-content: space-between;
  gap: var(--shag);
  padding: calc(var(--shag) * 2);
  border-radius: var(--radius);
  color: inherit;
  text-decoration: none;
}

.menyu-punkt--tekushchiy {
  background: var(--fon);
  font-weight: 600;
}

.menyu-punkt--budushchiy {
  color: var(--priglushyonnyy);
}

.menyu-skoro {
  flex: none;
  color: var(--priglushyonnyy);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

/* В шторке пункты выше — под палец. */
.modalnaya .menyu-punkt {
  align-items: center;
  min-height: 44px;
}

.soderzhimoe {
  flex: 1;
  min-width: 0;
  padding: calc(var(--shag) * 3);
}

/* Место под вкладки внизу: иначе последняя строка списка уходит под них. */
.obolochka--telefon .soderzhimoe {
  padding-bottom: calc(56px + var(--shag) * 3 + env(safe-area-inset-bottom));
}

.zaglushka-faza {
  color: var(--vnimanie);
}

.svezhest {
  margin: 0 0 calc(var(--shag) * 2);
  color: var(--priglushyonnyy);
  font-size: 0.875em;
}

.svezhest--staro {
  padding: calc(var(--shag) * 2);
  border-left: 3px solid var(--vnimanie);
  background: var(--vnimanie-fon);
  color: var(--tekst);
}

.svezhest--staro p {
  margin: 0;
}

/* Низ шторки: кто вошёл и «Выйти». */
.shtorka-niz {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: calc(var(--shag) * 2);
  width: 100%;
}

.shtorka-kto {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.2;
  overflow-wrap: anywhere;
}

.shtorka-vyhod {
  flex: none;
  min-height: 44px;
  padding: var(--shag) calc(var(--shag) * 3);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

/* Вкладки внизу телефона. Закреплены; высота 56 px плюс отступ под «бровь». */
.vkladki {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 2;
  display: flex;
  padding-bottom: env(safe-area-inset-bottom);
  background: var(--poverhnost);
  border-top: 1px solid var(--granitsa);
}

.vkladka {
  flex: 1 1 0;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 56px;
  padding: 0 var(--shag);
  border-top: 3px solid transparent;
  color: var(--priglushyonnyy);
  font-size: 13px;
  text-align: center;
  text-decoration: none;
}

/* Текущая — цветом и полосой сверху, не одним цветом. */
.vkladka--tekushchaya {
  color: var(--aktsent);
  border-top-color: var(--aktsent);
  font-weight: 600;
}
```

- [ ] **Step 6: Run the whole suite, types, lint**

Run: `cd frontend && npm run types && npm run lint && npm run test`
Expected: PASS. Если старый тест «выход есть и он в шапке» (1440 px) или «в меню видны все восемь разделов» падают — значит, широкая ветка изменилась: править `Layout`, не тесты.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/shell/Shtorka.tsx frontend/src/shell/Layout.tsx frontend/src/shell/shell.css frontend/tests/obolochka.test.tsx
git commit -m "Оболочка телефона: липкая шапка с разделом, шторка слева, вкладки внизу"
```

---

### Task 6: Приложение на главном экране — манифест, иконки, index.html

**Files:**
- Create: `frontend/public/manifest.webmanifest`, `frontend/public/favicon.svg`, `frontend/public/icons/kuhnya-192.png`, `frontend/public/icons/kuhnya-512.png`, `frontend/public/icons/kuhnya-maskable-512.png`, `frontend/public/apple-touch-icon.png`
- Modify: `frontend/index.html`
- Test: `frontend/tests/manifest.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/tests/manifest.test.ts
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from 'vitest'

// Файлы читаются с диска: манифест и index.html — не React, их проверяет
// только этот тест, а браузер молча проигнорировал бы неверный манифест.
const KOREN = join(__dirname, '..')
const manifest = () => JSON.parse(readFileSync(join(KOREN, 'public', 'manifest.webmanifest'), 'utf8')) as Record<string, unknown>
const html = () => readFileSync(join(KOREN, 'index.html'), 'utf8')

test('манифест: имя, запуск с блюд, окно без адресной строки, цвета из палитры', () => {
  const m = manifest()
  expect(m.name).toBe('Кухня')
  expect(m.short_name).toBe('Кухня')
  expect(m.start_url).toBe('/dishes')
  expect(m.scope).toBe('/')
  expect(m.display).toBe('standalone')
  expect(m.lang).toBe('ru')
  expect(m.background_color).toBe('#f2f2ef')
  expect(m.theme_color).toBe('#fbfbf9')
})

test('иконки из манифеста лежат в public, есть maskable и 512', () => {
  const icons = manifest().icons as { src: string; sizes: string; type: string; purpose?: string }[]
  expect(icons.length).toBeGreaterThanOrEqual(3)
  for (const icon of icons) {
    expect(existsSync(join(KOREN, 'public', icon.src))).toBe(true)
    expect(icon.type).toBe('image/png')
  }
  expect(icons.some((i) => i.sizes === '512x512' && i.purpose === 'maskable')).toBe(true)
  expect(icons.some((i) => i.sizes === '192x192')).toBe(true)
  expect(existsSync(join(KOREN, 'public', 'apple-touch-icon.png'))).toBe(true)
  expect(existsSync(join(KOREN, 'public', 'favicon.svg'))).toBe(true)
})

test('index.html подключает манифест, иконки, цвет строки состояния и вырез экрана', () => {
  const h = html()
  expect(h).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest"/)
  expect(h).toMatch(/<link rel="icon" href="\/favicon\.svg" type="image\/svg\+xml"/)
  expect(h).toMatch(/<link rel="apple-touch-icon" href="\/apple-touch-icon\.png"/)
  expect(h).toMatch(/<meta name="theme-color" content="#fbfbf9"/)
  expect(h).toMatch(/viewport-fit=cover/)
  expect(h).toMatch(/<meta name="mobile-web-app-capable" content="yes"/)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run tests/manifest.test.ts`
Expected: FAIL — нет `public/manifest.webmanifest`.

- [ ] **Step 3: Create the manifest, favicon and index.html**

```json
{
  "name": "Кухня",
  "short_name": "Кухня",
  "description": "R&D кухни: справочник, блюда, себестоимость",
  "lang": "ru",
  "start_url": "/dishes",
  "scope": "/",
  "display": "standalone",
  "background_color": "#f2f2ef",
  "theme_color": "#fbfbf9",
  "icons": [
    { "src": "/icons/kuhnya-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/kuhnya-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/icons/kuhnya-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

```svg
<!-- frontend/public/favicon.svg — квадрат акцентного цвета с буквой «К». -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#2f5ecb"/>
  <text x="32" y="45" text-anchor="middle" font-family="IBM Plex Sans, Arial, sans-serif" font-weight="600" font-size="38" fill="#fbfbf9">К</text>
</svg>
```

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <!-- viewport-fit=cover: страница заходит под вырез iPhone, отступы даёт env(safe-area-inset-*). -->
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#fbfbf9" />
    <!-- Добавленный на главный экран сайт открывается окном без адресной строки. -->
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-title" content="Кухня" />
    <link rel="manifest" href="/manifest.webmanifest" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <title>Кухня</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Render the PNG icons** (одноразовый скрипт во временной папке, в репозиторий кладутся только PNG)

```python
# narisovat_ikonki.py — запуск: cd backend && VIRTUAL_ENV= uv run python <путь>/narisovat_ikonki.py <путь к frontend/public>
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PUBLIC = Path(sys.argv[1])
SVG = (PUBLIC / "favicon.svg").read_text(encoding="utf-8")
# maskable: рисунок в безопасной зоне — фон на весь квадрат, буква мельче.
MASKABLE = SVG.replace('rx="12"', 'rx="0"').replace('font-size="38"', 'font-size="30"').replace('y="45"', 'y="43"')

def render(svg: str, size: int, out: Path) -> None:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": size, "height": size})
        pg.set_content(f'<html><body style="margin:0">{svg.replace("viewBox", f"width=\"{size}\" height=\"{size}\" viewBox")}</body></html>')
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out), omit_background=True)
        b.close()

(PUBLIC / "icons").mkdir(exist_ok=True)
render(SVG, 192, PUBLIC / "icons" / "kuhnya-192.png")
render(SVG, 512, PUBLIC / "icons" / "kuhnya-512.png")
render(MASKABLE, 512, PUBLIC / "icons" / "kuhnya-maskable-512.png")
render(SVG, 180, PUBLIC / "apple-touch-icon.png")
print("готово")
```

Проверить глазами (Read PNG): синий квадрат, белая «К» по центру.

- [ ] **Step 5: Run tests and build, then commit**

Run: `cd frontend && npx vitest run tests/manifest.test.ts && npm run build && ls dist/manifest.webmanifest dist/icons`
Expected: PASS (3 tests); в `dist/` лежат манифест и иконки (Vite копирует `public/` как есть).

```bash
git add frontend/index.html frontend/public frontend/tests/manifest.test.ts
git commit -m "Приложение на главном экране: манифест, иконки, цвет строки состояния"
```

---

### Task 7: Снимки и проверки на телефоне (`e2e/snimki.py`)

**Files:**
- Create: `frontend/e2e/snimki.py`, `frontend/e2e/README.md`, `frontend/e2e/.gitignore` (строка `snimki/`)

**Interfaces:**
- Produces: скрипт, который поднимает `vite preview` (или использует уже запущенный `dev` через `--port`), перехватывает `/api/`, снимает экраны на 360, 390 и 1440 px в `frontend/e2e/snimki/` и печатает проверки: горизонтальное переполнение, число строк блюд, минимальные цели касания. Код возврата 1 при провале проверки.

- [ ] **Step 1: Write the script**

```python
"""Снимки фронтенда на телефоне и компьютере с подменёнными ответами API.

Запуск (из backend/, где стоит Playwright):
    VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py            # соберёт и поднимет vite preview на 4173
    VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py --port 5174  # против уже запущенного dev-сервера

Проверки: горизонтальное переполнение 0 на всех экранах; в блюдах на 360 px
не меньше 8 строк в первом экране; кнопки и ссылки в шапке, содержимом и
вкладках не ниже 44 px при is_mobile. Провал — код возврата 1.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ZDES = Path(__file__).resolve().parent
FRONTEND = ZDES.parent
OUT = ZDES / "snimki"

ME = {"email": "chef@example.com", "display_name": "Бренд-шеф", "roles": ["chef", "developer"]}
SYNC = {
    "data_as_of": "2026-09-30T14:05:00+00:00", "changed_at": "2026-09-30T13:50:00+00:00", "stale": False,
    "books": [
        {"book": "kitchen", "title": "таблица кухни", "checked_at": "2026-09-30T14:05:00+00:00",
         "changed_at": "2026-09-30T13:50:00+00:00", "stale": False, "problem": None, "problem_since": None},
        {"book": "ingredient_cards", "title": "карточки ингредиентов", "checked_at": "2026-09-30T14:05:00+00:00",
         "changed_at": None, "stale": False, "problem": None, "problem_since": None},
    ],
}
BLYUDA = [
    ("B001", "Круассан с ветчиной и сыром", "Выпечка", "280", "84.66", "30.2", "69.8", "160", 2),
    ("B002", "Чизбургер классический", "Бургеры", "320", "112.40", "35.1", "64.9", "210", 0),
    ("B003", "Салат Цезарь с курицей", "Салаты", "390", "141.05", "36.2", "63.8", "250", 1),
    ("B004", "Картофель фри большой", "Гарниры", "150", "38.90", "25.9", "74.1", "180", 0),
    ("B005", "Хот-дог датский с брусничным соусом", "Хот-доги", "220", "97.30", "44.2", "55.8", "190", 1),
    ("B099", "Комбо «Семейное» 4 бургера, 2 картофеля, 4 напитка", "Комбо", "280", "1398.00", "499.3", "-399.3", "2400", 3),
    ("B010", "Лимонад домашний", "Напитки", "140", "22.15", "15.8", "84.2", "400", 0),
    ("B011", "Суп-пюре тыквенный", "Супы", None, "61.00", None, None, "300", 1),
    ("B012", "Пицца Пепперони 30 см", "Пицца", "590", "168.44", "28.5", "71.5", "520", 0),
    ("B013", "Наггетсы 9 шт", "Закуски", "230", "78.12", "34.0", "66.0", "170", 0),
    ("B014", "Кофе капучино 300 мл", "Напитки", "180", "31.60", "17.6", "82.4", "300", 0),
    ("B015", "Ролл с лососем и авокадо", "Роллы", "410", "155.90", "38.0", "62.0", "230", 2),
]
DISHES = [
    {"legacy_id": i, "name": n, "category": c, "status": "активное", "price_menu": p, "uc_rub": uc,
     "uc_percent": ucp, "margin_percent": m, "output_grams": o, "warnings": w}
    for i, n, c, p, uc, ucp, m, o, w in BLYUDA
]
DETAIL = {
    **DISHES[0], "protein_g": "12.4", "fat_g": "18.9", "carbs_g": "31.0", "kcal": "344", "kbju_coverage": "0.97",
    "components": [
        {"name": "Круассан сливочный 60гр", "short_name": "Круассан", "row_type": "main", "unit": "шт",
         "net_weight_g": "1", "gross_weight_g": "1.00", "price_per_unit": "49.50", "cost_rub": "0.83", "share_percent": "1.0"},
        {"name": "Ветчина варёная Останкино", "short_name": "Ветчина", "row_type": "main", "unit": "кг",
         "net_weight_g": "40", "gross_weight_g": "42.11", "price_per_unit": "620.00", "cost_rub": "26.11", "share_percent": "30.8"},
        {"name": "Сыр Гауда 45%", "short_name": "", "row_type": "main", "unit": "кг",
         "net_weight_g": "30", "gross_weight_g": "31.58", "price_per_unit": "890.00", "cost_rub": "28.11", "share_percent": "33.2"},
        {"name": "Контейнер бумажный без крышки 207х127х55 крафт/чёрный", "short_name": "", "row_type": "packaging",
         "unit": "шт", "net_weight_g": "1", "gross_weight_g": None, "price_per_unit": "12.40", "cost_rub": "12.40", "share_percent": "14.6"},
    ],
    "warning_texts": ["Штучный «Круассан сливочный 60гр»: нетто 1 г при весе штуки 60 г — похоже, вписано количество штук, а не граммы; себестоимость занижена"],
}
INGREDIENTS = [
    {"id": int(i), "legacy_id": i, "name": n, "category": c, "unit": u, "status": s, "price_per_kg": p,
     "weight_per_piece_g": w, "has_card": h}
    for i, n, c, u, s, p, w, h in [
        ("1", "Круассан сливочный 60гр", "Выпечка", "шт", "активный", "49.50", "60", True),
        ("2", "Ветчина варёная Останкино", "Мясо", "кг", "активный", "620.00", None, True),
        ("3", "Сыр Гауда 45%", "Сыры", "кг", "активный", "890.00", None, False),
        ("5", "Контейнер бумажный без крышки 207х127х55 крафт/чёрный", "Упаковка", "шт", "активный", "12.40", None, False),
        ("6", "Свёкла", "Овощи", "кг", "архив", None, None, False),
        ("7", "Огурцы резаные", "Овощи", "кг", "активный", "180.00", None, True),
        ("8", "Котлета говяжья 100 г", "Мясо", "шт", "активный", "48.00", "100", True),
        ("9", "Булочка для бургера с кунжутом", "Выпечка", "шт", "активный", "9.80", "70", True),
        ("10", "Лосось слабосолёный", "Рыба", "кг", "активный", "1480.00", None, False),
        ("12", "Молоко 3,2%", "Молочные продукты", "л", "активный", "78.00", None, True),
    ]
]
RECON = {
    "total": 101, "linked": 85, "needs_human": 16,
    "rows": [
        {"card_id": 1, "name": "Соус барбекю РБК", "link_status": "ambiguous", "supplier": "Метро",
         "candidates": [{"ingredient_id": 21, "legacy_id": "21", "name": "Соус барбекю РБК", "score": None},
                        {"ingredient_id": 22, "legacy_id": "22", "name": "Соус барбекю РБК", "score": None}]},
        {"card_id": 2, "name": "Огурцы не резаные", "link_status": "candidate", "supplier": "Восток",
         "candidates": [{"ingredient_id": 7, "legacy_id": "7", "name": "Огурцы резаные", "score": None}]},
        {"card_id": 3, "name": "Тостовый хлеб", "link_status": "orphan", "supplier": "Хлебозавод", "candidates": []},
    ],
}


def otvet(route, request, state):
    path = request.url.split("/api", 1)[1].split("?")[0]
    def json_(telo, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(telo))
    if path == "/me":
        return json_(ME) if state["me"] == 200 else json_({"detail": "нужен токен"}, 401)
    if path == "/sync":
        return json_(state.get("sync", SYNC))
    if path == "/dishes":
        return json_(DISHES)
    if path.startswith("/dishes/"):
        return json_(DETAIL)
    if path == "/ingredients":
        return json_(INGREDIENTS)
    if path == "/reconciliation":
        return json_(RECON)
    if path == "/auth/refresh":
        return json_({"detail": "сессия"}, 401)
    return json_({"detail": "not found"}, 404)


def zhdat_port(port: int, timeout: float = 90) -> None:
    konets = time.time() + timeout
    while time.time() < konets:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"сервер на {port} не поднялся")


class Proverki:
    def __init__(self) -> None:
        self.provaly: list[str] = []

    def snimok(self, page: Page, imya: str, *, full: bool = False, mobile: bool = False) -> None:
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"{imya}.png"), full_page=full)
        perepolnenie = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        print(f"{imya}: переполнение {perepolnenie}px")
        if perepolnenie > 0:
            self.provaly.append(f"{imya}: горизонтальное переполнение {perepolnenie}px")
        if mobile:
            melkie = page.evaluate(
                """() => [...document.querySelectorAll('header button, header a, main button, main a, nav a, [role=dialog] button, [role=dialog] a')]
                    .filter(el => el.offsetParent !== null || el.closest('[role=dialog]'))
                    .map(el => ({t: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40), r: el.getBoundingClientRect()}))
                    .filter(x => x.r.width > 0 && x.r.height > 0 && (x.r.height < 44 || x.r.width < 44))
                    .map(x => `${x.t} ${Math.round(x.r.width)}×${Math.round(x.r.height)}`)"""
            )
            if melkie:
                self.provaly.append(f"{imya}: цели меньше 44 px — {melkie[:6]}")

    def strok(self, page: Page, imya: str, minimum: int) -> None:
        # Строки, чей верх помещается в первый экран.
        n = page.evaluate("[...document.querySelectorAll('.stroka, .spisok > li')].filter(li => li.getBoundingClientRect().top < window.innerHeight).length")
        print(f"{imya}: строк в первом экране {n}")
        if n < minimum:
            self.provaly.append(f"{imya}: строк в первом экране {n}, нужно не меньше {minimum}")


def snyat(base: str) -> Proverki:
    OUT.mkdir(exist_ok=True)
    p = Proverki()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for shirina, vysota, prefix, mobile in ((360, 780, "tel360", True), (390, 844, "tel390", True), (1440, 900, "pk", False)):
            state = {"me": 401}
            ctx = browser.new_context(viewport={"width": shirina, "height": vysota}, device_scale_factor=2,
                                      is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            page.route(re.compile(rf"^{re.escape(base)}/api/"), lambda r, q: otvet(r, q, state))
            page.goto(f"{base}/dishes")
            page.wait_for_selector("form", timeout=15000)
            p.snimok(page, f"{prefix}-01-vhod", mobile=mobile)

            state["me"] = 200
            page.goto(f"{base}/dishes")
            page.wait_for_selector(".stroka, .spisok, .tablitsa", timeout=15000)
            p.snimok(page, f"{prefix}-02-blyuda", mobile=mobile)
            if mobile:
                p.strok(page, f"{prefix}-02-blyuda", 8)
                page.click("button[aria-label='Разделы']")
                page.wait_for_selector("[role=dialog]")
                p.snimok(page, f"{prefix}-03-shtorka", mobile=mobile)
                page.keyboard.press("Escape")
                knopka = page.locator("button", has_text=re.compile("^(Фильтры|Сортировка и фильтры)"))
                if knopka.count():
                    knopka.first.click()
                    page.wait_for_timeout(300)
                    p.snimok(page, f"{prefix}-04-otbor", mobile=mobile)
                    page.keyboard.press("Escape")

            page.goto(f"{base}/dishes/B001")
            page.wait_for_selector(".kartochka h1", timeout=15000)
            p.snimok(page, f"{prefix}-05-kartochka", full=True, mobile=mobile)

            page.goto(f"{base}/ingredients")
            page.wait_for_selector(".stroka, .spisok, .tablitsa", timeout=15000)
            p.snimok(page, f"{prefix}-06-spravochnik", mobile=mobile)

            page.goto(f"{base}/reconciliation")
            page.wait_for_selector("main", timeout=15000)
            page.wait_for_timeout(600)
            p.snimok(page, f"{prefix}-07-sverka", full=True, mobile=mobile)

            state["sync"] = {**SYNC, "stale": True, "books": [{**SYNC["books"][0], "stale": True, "problem": "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"}, SYNC["books"][1]]}
            page.goto(f"{base}/dishes")
            page.wait_for_selector(".svezhest--staro", timeout=15000)
            p.snimok(page, f"{prefix}-08-polosa", mobile=mobile)
            ctx.close()
        browser.close()
    return p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="уже запущенный dev-сервер; без него — сборка и vite preview на 4173")
    args = parser.parse_args()
    server = None
    if args.port is None:
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND, check=True, shell=sys.platform == "win32")
        server = subprocess.Popen(["npm", "run", "preview", "--", "--port", "4173", "--strictPort"], cwd=FRONTEND, shell=sys.platform == "win32")
        port = 4173
    else:
        port = args.port
    try:
        zhdat_port(port)
        p = snyat(f"http://127.0.0.1:{port}")
    finally:
        if server is not None:
            server.terminate()
    if p.provaly:
        print("ПРОВАЛЫ:")
        for stroka in p.provaly:
            print(" -", stroka)
        return 1
    print(f"Все проверки прошли, снимки в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`frontend/e2e/README.md`:

```markdown
# Снимки экранов на телефоне

`snimki.py` снимает экраны на 360, 390 и 1440 px с подменёнными ответами API
и проверяет: нет горизонтального переполнения, в блюдах на телефоне не
меньше 8 строк в первом экране, кнопки и ссылки не ниже 44 px.

Запуск из `backend/` (там установлен Playwright):

    VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py

Снимки — в `e2e/snimki/` (не в git). Не входит в блокирующий CI: это
приёмочная проверка перед PR и кандидат в ночной прогон.
```

`frontend/e2e/.gitignore`: `snimki/`.

- [ ] **Step 2: Run the script, look at the pictures, fix what it finds**

Run: `cd backend && VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py`
Expected: код 0 после задачи 5; на этой задаче допустимы провалы «цели меньше 44 px» в строках списка и «строк меньше 8» — их чинят задачи 10–12 (PR-2); всё остальное (переполнение, шапка, шторка, вкладки) — чинить здесь. Открыть `tel360-02-blyuda.png`, `tel360-03-shtorka.png`, `pk-02-blyuda.png` и убедиться глазами: шторка поверх страницы с затемнением, вкладки внизу, шапка с «Блюда», компьютер как прежде.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e
git commit -m "Снимки экранов на телефоне: скрипт проверки переполнения, строк и целей касания"
```

---

### Task 8: Итог PR-1 — ревью, сборка, PR

- [ ] **Step 1: Full check**

Run: `cd frontend && npm run types && npm run lint && npm run test && npm run build`
Expected: всё зелёное; `git ls-files --eol frontend | grep -v 'w/lf' | grep -v 'w/none'` — только PNG.

- [ ] **Step 2: Итоговое ревью ветки** (агент-ревьюер на модели чата): спека разделы 2.1–2.5, 2.9–2.11; компьютер без изменений; общие файлы этапа 5 не тронуты (`git diff main --stat` не содержит `Nav.tsx`, `razdely.ts`, `App.tsx`, `api/`).

- [ ] **Step 3: Push and open the PR** «Мобильный вид, часть 1: шапка, шторка, вкладки, манифест» с чек-листом приёмки на телефоне (шторка, затемнение, Escape, вкладки, «Добавить на главный экран»), снимками `tel360-02`, `tel360-03`, `pk-02` в описании. Слияние — по слову Александра; сообщить соседнему чату, что оболочка в `main`.

---
## PR-2 «Строки и панель отбора» — задачи 9–13

### Task 9: Шеврон в наборе иконок

**Files:**
- Modify: `frontend/src/ui/Icons.tsx` (добавить в конец)

**Interfaces:**
- Produces: `ShevronIcon({ className?, napravlenie: 'vpravo' | 'vniz' | 'vverh' })` — 14 px, тот же стиль, что у остальных.

- [ ] **Step 1: Add the icon** (тест — в задаче 10, через строку списка)

```tsx
/**
 * Шеврон строки списка на телефоне: вправо — строка открывает карточку,
 * вниз/вверх — раскрывает и сворачивает подробности.
 */
export function ShevronIcon({ className, napravlenie }: SvoystvaIkonki & { napravlenie: 'vpravo' | 'vniz' | 'vverh' }) {
  return (
    <MalayaIkonka className={className}>
      {napravlenie === 'vpravo' && <path d="M7 4l6 6-6 6" />}
      {napravlenie === 'vniz' && <path d="M4 7l6 6 6-6" />}
      {napravlenie === 'vverh' && <path d="M4 13l6-6 6 6" />}
    </MalayaIkonka>
  )
}
```

- [ ] **Step 2: Types and lint, commit**

Run: `cd frontend && npm run types && npm run lint`

```bash
git add frontend/src/ui/Icons.tsx
git commit -m "Иконки: шеврон для строк списка"
```

---

### Task 10: Роль колонки на телефоне и строка списка

**Files:**
- Create: `frontend/src/ui/StrokaSpiska.tsx`
- Modify: `frontend/src/ui/DataTable.tsx` (тип `Column`, узкая ветка), `frontend/src/ui/table.css` (строка, красная строка)
- Test: `frontend/tests/strokaSpiska.test.tsx` (новый), `frontend/tests/tablitsa.test.tsx` (колонки и два теста), `frontend/tests/tablitsaSFiltrami.test.tsx` и `frontend/tests/zagolovok.test.tsx` (только `priority` → `uzkiy` в описаниях колонок)

**Interfaces:**
- Produces:
  ```ts
  export type RolUzkogo = 'zagolovok' | 'podpis' | 'znachenie' | 'podrobno' | 'skryto'
  export type Column<T> = OpisanieKolonki<T> & {
    align?: 'left' | 'right'
    uzkiy: RolUzkogo
    nazvanieUzkoe?: string          // '' — без подписи
    render: (row: T) => ReactNode
    renderUzkiy?: (row: T) => ReactNode   // null/'' — часть подписи не показывается
  }
  export function StrokaSpiska<T>(props: { columns: Column<T>[]; row: T; klass?: string; onOpen?: (row: T) => void }): JSX.Element
  ```
  Соответствие старому: `priority: 'always'` → `uzkiy: 'znachenie'` (или `'zagolovok'` у первой текстовой), `priority: 'wide'` → `uzkiy: 'podrobno'`.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/tests/strokaSpiska.test.tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'

import { StrokaSpiska } from '../src/ui/StrokaSpiska'
import type { Column } from '../src/ui/DataTable'

type Pozitsiya = { id: string; imya: string; kategoriya: string; tsena: string; ves: string | null; zamechaniya: number }

const STROKA: Pozitsiya = { id: '12', imya: 'Свёкла', kategoriya: 'Овощи', tsena: '96,00 ₽/кг', ves: null, zamechaniya: 2 }

const KOLONKI: Column<Pozitsiya>[] = [
  { key: 'id', title: 'id', uzkiy: 'podpis', render: (r) => r.id },
  { key: 'imya', title: 'Наименование', uzkiy: 'zagolovok', render: (r) => r.imya },
  { key: 'kategoriya', title: 'Категория', uzkiy: 'podpis', render: (r) => r.kategoriya },
  {
    key: 'zamechaniya',
    title: 'Замечания',
    uzkiy: 'podpis',
    render: (r) => r.zamechaniya,
    renderUzkiy: (r) => (r.zamechaniya > 0 ? `${r.zamechaniya} замечания` : null),
  },
  { key: 'tsena', title: 'Цена за единицу', nazvanieUzkoe: 'цена', uzkiy: 'znachenie', align: 'right', render: (r) => r.tsena },
  { key: 'ves', title: 'Вес 1 шт', uzkiy: 'podrobno', render: (r) => r.ves ?? '—' },
  { key: 'status', title: 'Статус', uzkiy: 'skryto', render: () => 'активный' },
]

test('заголовок, подпись через точки, значение с подписью; скрытое не рисуется', () => {
  render(<ul><StrokaSpiska columns={KOLONKI} row={STROKA} /></ul>)
  const stroka = screen.getByRole('listitem')
  expect(within(stroka).getByText('Свёкла')).toHaveClass('stroka-zagolovok')
  expect(stroka.querySelector('.stroka-podpis')).toHaveTextContent('12 · Овощи · 2 замечания')
  const znachenie = stroka.querySelector('.stroka-znachenie')!
  expect(znachenie).toHaveTextContent('цена')
  expect(znachenie).toHaveTextContent('96,00 ₽/кг')
  expect(screen.queryByText('активный')).not.toBeInTheDocument()
  expect(screen.queryByText('Категория')).not.toBeInTheDocument()
})

test('часть подписи, вернувшая null, не оставляет лишней точки', () => {
  render(<ul><StrokaSpiska columns={KOLONKI} row={{ ...STROKA, zamechaniya: 0 }} /></ul>)
  expect(screen.getByRole('listitem').querySelector('.stroka-podpis')).toHaveTextContent(/^12 · Овощи$/)
})

test('без onOpen строка раскрывает подробности тапом, с aria-expanded', async () => {
  render(<ul><StrokaSpiska columns={KOLONKI} row={STROKA} /></ul>)
  const knopka = screen.getByRole('button', { name: /Свёкла/ })
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Вес 1 шт')).not.toBeInTheDocument()

  await userEvent.click(knopka)
  expect(knopka).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText('Вес 1 шт')).toBeInTheDocument()
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('с onOpen строка открывает карточку, подробностей нет, шеврон вправо', async () => {
  const otkryto: string[] = []
  render(<ul><StrokaSpiska columns={KOLONKI} row={STROKA} onOpen={(r) => otkryto.push(r.id)} /></ul>)
  const knopka = screen.getByRole('button', { name: /Свёкла/ })
  expect(knopka).not.toHaveAttribute('aria-expanded')
  await userEvent.click(knopka)
  expect(otkryto).toEqual(['12'])
  expect(screen.queryByText('Вес 1 шт')).not.toBeInTheDocument()
})

test('без onOpen и без подробностей строка — не кнопка', () => {
  const bezPodrobnostey = KOLONKI.filter((k) => k.uzkiy !== 'podrobno')
  render(<ul><StrokaSpiska columns={bezPodrobnostey} row={STROKA} /></ul>)
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.getByText('Свёкла')).toBeInTheDocument()
})

test('класс строки — на li', () => {
  render(<ul><StrokaSpiska columns={KOLONKI} row={STROKA} klass="stroka--ubytok" /></ul>)
  expect(screen.getByRole('listitem')).toHaveClass('stroka', 'stroka--ubytok')
})
```

В `tests/tablitsa.test.tsx` заменить описание колонок и два теста:

```tsx
const kolonki: Column<Blyudo>[] = [
  { key: 'nazvanie', title: 'Название', uzkiy: 'zagolovok', render: (r) => r.nazvanie },
  { key: 'uc', title: 'Себестоимость', uzkiy: 'znachenie', align: 'right', render: (r) => r.uc },
  { key: 'kategoriya', title: 'Категория', uzkiy: 'podrobno', render: (r) => r.kategoriya },
]
```

```tsx
test('остальное раскрывается тапом по строке', async () => {
  setViewport(360)
  narisovat()
  expect(screen.queryByText('Соус-топпинг')).not.toBeInTheDocument()
  // Отдельной ссылки «Подробнее» нет — раскрывает сама строка.
  expect(screen.queryByRole('button', { name: /подробнее/i })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /Кетчуп/ }))
  expect(screen.getByText('Соус-топпинг')).toBeInTheDocument()
})
```

Тест «карточку на узком экране можно открыть с клавиатуры» остаётся как есть (строка — кнопка с именем «Кетчуп…»). В `tests/tablitsaSFiltrami.test.tsx` и `tests/zagolovok.test.tsx` заменить `priority: 'always'` на `uzkiy: 'znachenie'` (у первой текстовой колонки — `'zagolovok'`), `priority: 'wide'` → `uzkiy: 'podrobno'`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/strokaSpiska.test.tsx tests/tablitsa.test.tsx`
Expected: FAIL — нет `StrokaSpiska`, типы `uzkiy` не знают.

- [ ] **Step 3: Write `StrokaSpiska.tsx`**

```tsx
// frontend/src/ui/StrokaSpiska.tsx
import { Fragment, useState, type ReactNode } from 'react'

import type { Column } from './DataTable'
import { ShevronIcon } from './Icons'

type Props<T> = {
  columns: Column<T>[]
  row: T
  klass?: string | undefined
  onOpen?: ((row: T) => void) | undefined
}

// Пустая часть подписи — не рисуется и точки после себя не оставляет.
function pusto(chast: ReactNode): boolean {
  return chast === null || chast === undefined || chast === '' || chast === false
}

/**
 * Строка списка на телефоне. Слева заголовок и подпись мелким через « · »,
 * справа — значения столбиком с подписями, выровненные по правому краю (в
 * прежней сетке `1fr auto` третье значение уезжало в первую колонку).
 * Строка целиком — кнопка: открывает карточку (`onOpen`, шеврон вправо) или
 * раскрывает подробности (шеврон вниз/вверх, `aria-expanded`). Отдельной
 * ссылки «Подробнее» нет — решение 30.09.2026.
 */
export function StrokaSpiska<T>({ columns, row, klass, onOpen }: Props<T>) {
  const [raskryto, raskryt] = useState(false)

  const zagolovok = columns.find((k) => k.uzkiy === 'zagolovok')
  const podpisi = columns
    .filter((k) => k.uzkiy === 'podpis')
    .map((k) => (k.renderUzkiy ?? k.render)(row))
    .filter((chast) => !pusto(chast))
  const znacheniya = columns.filter((k) => k.uzkiy === 'znachenie')
  // Открываемая строка подробностей не показывает: всё остальное — в карточке.
  const podrobno = onOpen ? [] : columns.filter((k) => k.uzkiy === 'podrobno')
  const raskryvaemaya = podrobno.length > 0

  const telo = (
    <>
      <span className="stroka-levo">
        <span className="stroka-zagolovok">{zagolovok ? (zagolovok.renderUzkiy ?? zagolovok.render)(row) : null}</span>
        {podpisi.length > 0 && (
          <span className="stroka-podpis">
            {podpisi.map((chast, nomer) => (
              <Fragment key={nomer}>
                {nomer > 0 && ' · '}
                {chast}
              </Fragment>
            ))}
          </span>
        )}
      </span>
      {znacheniya.length > 0 && (
        <span className="stroka-pravo">
          {znacheniya.map((k) => {
            const podpis = k.nazvanieUzkoe ?? k.title
            return (
              <span key={k.key} className="stroka-znachenie">
                {podpis !== '' && <span className="stroka-znachenie-podpis">{podpis}</span>}
                {(k.renderUzkiy ?? k.render)(row)}
              </span>
            )
          })}
        </span>
      )}
      {(onOpen || raskryvaemaya) && (
        <ShevronIcon className="stroka-shevron" napravlenie={onOpen ? 'vpravo' : raskryto ? 'vverh' : 'vniz'} />
      )}
    </>
  )

  return (
    <li className={klass ? `stroka ${klass}` : 'stroka'}>
      {onOpen ? (
        <button type="button" className="stroka-telo" onClick={() => onOpen(row)}>
          {telo}
        </button>
      ) : raskryvaemaya ? (
        <button type="button" className="stroka-telo" aria-expanded={raskryto} onClick={() => raskryt(!raskryto)}>
          {telo}
        </button>
      ) : (
        <div className="stroka-telo">{telo}</div>
      )}
      {raskryto && (
        <dl className="stroka-podrobno">
          {podrobno.map((k) => (
            <div key={k.key}>
              <dt>{k.title}</dt>
              <dd>{(k.renderUzkiy ?? k.render)(row)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
```

- [ ] **Step 4: Change `DataTable.tsx`**

Тип `Column` — по блоку Interfaces выше (удалить `priority`). Узкая ветка:

```tsx
function Uzkiy<T>({ columns, rows, rowKey, rowClass, onOpen }: Props<T>) {
  return (
    <ul className="spisok">
      {rows.map((row) => (
        <StrokaSpiska key={rowKey(row)} columns={columns} row={row} klass={rowClass?.(row)} onOpen={onOpen} />
      ))}
    </ul>
  )
}
```

Удалить прежний `Kartochka` и импорт `useState`; добавить `import { StrokaSpiska } from './StrokaSpiska'`. Ветка `Shirokaya` и `DataTable` без изменений.

- [ ] **Step 5: Replace list styles in `table.css`** (блоки `.spisok`, `.spisok-glavnoe`, `.raskryt`, `.spisok-podrobno` — удалить; `.stroka--ubytok` заменить)

```css
/* Себестоимость выше цены меню — красный фон и у строки таблицы, и у
   строки списка, и при наведении: селекторы той же силы, что фон строки. */
.tablitsa tbody tr.stroka--ubytok,
.tablitsa tbody tr.stroka--ubytok:hover,
.spisok > li.stroka--ubytok {
  background: var(--oshibka-fon);
}

.spisok {
  list-style: none;
  margin: 0;
  padding: 0;
}

.spisok > li {
  background: var(--poverhnost);
  border-bottom: 1px solid var(--granitsa);
}

/* Строка — кнопка во всю ширину: заголовок и подпись слева, значения
   справа, шеврон. Высота около 64 px — 8–10 строк на 360×780. */
.stroka-telo {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 0 calc(var(--shag) * 2);
  width: 100%;
  min-height: 56px;
  margin: 0;
  padding: calc(var(--shag) * 2) calc(var(--shag) * 2) calc(var(--shag) * 2) calc(var(--shag) * 3);
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
}

button.stroka-telo {
  cursor: pointer;
}

.stroka-levo {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.stroka-zagolovok {
  font-size: 15px;
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.stroka-podpis {
  color: var(--priglushyonnyy);
  font-size: 12px;
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.stroka-pravo {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  text-align: right;
}

.stroka-znachenie {
  display: flex;
  align-items: baseline;
  gap: var(--shag);
  white-space: nowrap;
}

.stroka-znachenie-podpis {
  color: var(--priglushyonnyy);
  font-size: 11px;
}

.stroka-shevron {
  flex: none;
  color: var(--priglushyonnyy);
}

.stroka-podrobno {
  margin: 0;
  padding: 0 calc(var(--shag) * 3) calc(var(--shag) * 2);
}

.stroka-podrobno > div {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--shag);
  padding: calc(var(--shag) / 2) 0;
}

.stroka-podrobno dt {
  color: var(--priglushyonnyy);
}

.stroka-podrobno dd {
  margin: 0;
  text-align: right;
}
```

`.spisok .vpravo { text-align: right }` — оставить (нужно `Shirokaya`? нет — `.tablitsa .vpravo` отдельно; строку `.spisok .vpravo` удалить).

- [ ] **Step 6: Run the suite, types, lint; commit**

Run: `cd frontend && npm run types && npm run lint && npm run test`
Expected: PASS. Экраны `Dishes.tsx`/`Ingredients.tsx` ещё используют `priority` — типы упадут: задача 11 идёт следом, поэтому **в этой задаче** временно заменить в них `priority: 'always'` → `uzkiy: 'znachenie'` (первую текстовую — `'zagolovok'`), `'wide'` → `'podrobno'`, чтобы сборка была зелёной; настоящие роли — в задаче 11.

```bash
git add frontend/src/ui/StrokaSpiska.tsx frontend/src/ui/DataTable.tsx frontend/src/ui/table.css frontend/src/pages/Dishes.tsx frontend/src/pages/Ingredients.tsx frontend/tests/strokaSpiska.test.tsx frontend/tests/tablitsa.test.tsx frontend/tests/tablitsaSFiltrami.test.tsx frontend/tests/zagolovok.test.tsx
git commit -m "Строка списка на телефоне: заголовок, подпись, значения с подписями, без «Подробнее»"
```

---

### Task 11: Роли колонок блюд и справочника

**Files:**
- Create: `frontend/src/domain/slova.ts`
- Modify: `frontend/src/pages/Dishes.tsx`, `frontend/src/pages/Ingredients.tsx`
- Test: `frontend/tests/slova.test.ts` (новый), `frontend/tests/blyuda.test.tsx`, `frontend/tests/spravochnik.test.tsx` (добавить тесты 360 px)

**Interfaces:**
- Produces: `zamechaniyaSlovami(n: number): string | null` — «1 замечание», «2 замечания», «5 замечаний», `null` при 0.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/tests/slova.test.ts
import { expect, test } from 'vitest'

import { zamechaniyaSlovami } from '../src/domain/slova'

test('склонение «замечание»', () => {
  expect(zamechaniyaSlovami(0)).toBeNull()
  expect(zamechaniyaSlovami(1)).toBe('1 замечание')
  expect(zamechaniyaSlovami(2)).toBe('2 замечания')
  expect(zamechaniyaSlovami(4)).toBe('4 замечания')
  expect(zamechaniyaSlovami(5)).toBe('5 замечаний')
  expect(zamechaniyaSlovami(11)).toBe('11 замечаний')
  expect(zamechaniyaSlovami(21)).toBe('21 замечание')
  expect(zamechaniyaSlovami(112)).toBe('112 замечаний')
})
```

Добавить в `tests/blyuda.test.tsx`:

```tsx
test('на 360 px строка блюда: название, id и категория подписью, UC и маржа с подписями, всё — кнопка в карточку', async () => {
  setViewport(360)
  narisovat()
  const stroka = (await screen.findByText('Круасан с мортаделой')).closest('li')!
  expect(stroka.querySelector('.stroka-podpis')).toHaveTextContent('B001 · Блюдо · 1 замечание')
  const znacheniya = [...stroka.querySelectorAll('.stroka-znachenie')].map((el) => el.textContent)
  expect(znacheniya[0]).toMatch(/^UC/)
  expect(znacheniya[0]).toContain('84,66 ₽')
  expect(znacheniya[1]).toMatch(/^маржа/)
  expect(znacheniya[1]).toContain('77,1 %')
  // Статус, цена меню, UC % и выход на телефоне не показываются — они в карточке.
  expect(stroka).not.toHaveTextContent('369,00 ₽')
  expect(stroka).not.toHaveTextContent('22,9 %')
  expect(screen.queryByRole('button', { name: /подробнее/i })).not.toBeInTheDocument()

  await userEvent.click(within(stroka).getByRole('button'))
  expect(put).toBe('/dishes/B001')
})

test('на 360 px красная строка — у li, а не только у tr', async () => {
  setViewport(360)
  narisovat()
  const stroka = (await screen.findByText('Салат овощной')).closest('li')!
  expect(stroka).toHaveClass('stroka--ubytok')
  expect(stroka).toHaveTextContent('Выше цены меню')
})
```

Добавить в `tests/spravochnik.test.tsx`. Фикстуры файла: «Яйцо» (legacy_id 12, шт, `has_card: false`) и «Молоко» (1000, л, `has_card: true`); если `narisovat()` по умолчанию отдаёт не весь список, передать ответ с этими двумя позициями так же, как это делают соседние тесты файла:

```tsx
test('на 360 px строка справочника: подпись «id · категория · ед.», цена и слова о карточке; вес и статус — тапом', async () => {
  setViewport(360)
  narisovat()
  const stroka = (await screen.findByText('Яйцо')).closest('li')!
  // Порядок подписи — порядок колонок таблицы: id, категория, единица.
  expect(stroka.querySelector('.stroka-podpis')).toHaveTextContent(/^12 · [^·]+ · шт$/)
  expect(stroka).toHaveTextContent('нет карточки')
  expect(stroka).not.toHaveTextContent('✓')
  expect(stroka).not.toHaveTextContent('○')
  expect(within(stroka).queryByText('Статус')).not.toBeInTheDocument()

  const knopka = within(stroka).getByRole('button')
  expect(knopka).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(knopka)
  expect(within(stroka).getByText('Вес 1 шт')).toBeInTheDocument()
  expect(within(stroka).getByText('Статус')).toBeInTheDocument()

  const sKartochkoy = (await screen.findByText('Молоко')).closest('li')!
  expect(sKartochkoy).toHaveTextContent('карточка есть')
  expect(sKartochkoy.querySelector('.stroka-podpis')).toHaveTextContent(/^1000 · [^·]+ · л$/)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/slova.test.ts tests/blyuda.test.tsx tests/spravochnik.test.tsx`
Expected: FAIL — нет `slova.ts`; подписи и роли не те.

- [ ] **Step 3: Write `slova.ts` and set the column roles**

```ts
// frontend/src/domain/slova.ts
/** Форма слова для числа: 1 замечание, 2 замечания, 5 замечаний; 11–14 — «многие». */
function forma(n: number, odno: string, dva: string, pyat: string): string {
  const sto = n % 100
  if (sto >= 11 && sto <= 14) return pyat
  const desyat = sto % 10
  if (desyat === 1) return odno
  if (desyat >= 2 && desyat <= 4) return dva
  return pyat
}

/** «N замечаний» для подписи строки на телефоне; ноль — ничего: «0 замечаний» шумит. */
export function zamechaniyaSlovami(n: number): string | null {
  if (n <= 0) return null
  return `${n} ${forma(n, 'замечание', 'замечания', 'замечаний')}`
}
```

`Dishes.tsx`, колонки (заменить все `priority`, добавить подписи):

| key | uzkiy | nazvanieUzkoe / renderUzkiy |
|---|---|---|
| `id` | `podpis` | — |
| `name` | `zagolovok` | — |
| `category` | `podpis` | — |
| `status` | `skryto` | — |
| `price` | `skryto` | — |
| `uc` | `znachenie` | `nazvanieUzkoe: 'UC'` |
| `ucp` | `skryto` | — |
| `margin` | `znachenie` | `nazvanieUzkoe: 'маржа'` |
| `output` | `skryto` | — |
| `warnings` | `podpis` | `renderUzkiy: (r) => { const t = zamechaniyaSlovami(r.warnings); return t ? <span className="zamechaniya">{t}</span> : null }` |

`Ingredients.tsx`, колонки:

| key | uzkiy | nazvanieUzkoe / renderUzkiy |
|---|---|---|
| `id` | `podpis` | — |
| `name` | `zagolovok` | — |
| `category` | `podpis` | — |
| `unit` | `podpis` | — |
| `price` | `znachenie` | `nazvanieUzkoe: 'цена'` |
| `ves` | `podrobno` | — |
| `status` | `podrobno` | — |
| `card` | `znachenie` | `nazvanieUzkoe: ''`, `renderUzkiy: (r) => r.has_card ? <span className="kartochka-est">карточка есть</span> : <span className="net-kartochki">нет карточки</span>` |

Порядок подписи — порядок колонок в массиве (`id · категория · ед.`): колонки таблицы не переставлять (ТЗ: «Ед.» сразу после категории), тест выше ждёт именно этот порядок. Добавить в `pages.css`: `.kartochka-est { color: var(--priglushyonnyy); font-size: 12px; } .net-kartochki { font-size: 12px; }` (цвет у `.net-kartochki` уже есть).

- [ ] **Step 4: Run tests, types, lint; commit**

Run: `cd frontend && npm run types && npm run lint && npm run test`
Expected: PASS.

```bash
git add frontend/src/domain/slova.ts frontend/src/pages/Dishes.tsx frontend/src/pages/Ingredients.tsx frontend/src/pages/pages.css frontend/tests/slova.test.ts frontend/tests/blyuda.test.tsx frontend/tests/spravochnik.test.tsx
git commit -m "Блюда и справочник на телефоне: роли колонок, подписи чисел, слова о карточке"
```

---

### Task 12: Панель отбора снизу

**Files:**
- Create: `frontend/src/ui/PanelOtbora.tsx`
- Modify: `frontend/src/ui/PanelTablitsy.tsx` (узкая ветка), `frontend/src/ui/panelTablitsy.css`
- Test: `frontend/tests/panelOtbora.test.tsx` (новый), `frontend/tests/panelTablitsy.test.tsx` (тесты 360 px), `frontend/tests/tablitsaSFiltrami.test.tsx`, `frontend/tests/blyuda.test.tsx`, `frontend/tests/spravochnik.test.tsx` (места с `combobox` «Сортировка» и кнопкой «Фильтры»)

**Interfaces:**
- Consumes: `ModalnayaPanel` (задача 2), `Galochki`, `variantySortirovki`, `kodSortirovki`, `razobratKodSortirovki`.
- Produces:
  ```ts
  type Props<T> = {
    knopka: RefObject<HTMLButtonElement>
    kolonki: readonly OpisanieKolonki<T>[]
    naideno: number
    sortirovka: Sortirovka | null
    onSortirovka: (s: Sortirovka | null) => void
    gruppy: readonly GruppaFiltra[]
    onVybor: (kolonka: string, kody: string[]) => void
    onSbrositVsyo: () => void
  }
  export function PanelOtbora<T>(props: Props<T>): JSX.Element
  ```
  Кнопка: «Сортировка и фильтры» / «Сортировка и фильтры (N)» (N — включённые группы), `aria-haspopup="dialog"`, `aria-expanded`, `aria-controls` при открытой. Диалог «Сортировка и фильтры»: группа `radio` «Сортировка» («Без сортировки» + `variantySortirovki`), группы галочек в `<details>` (включённые раскрыты), низ: «Показать N» (закрывает) и «Сбросить всё» (при включённых, не закрывает).

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/tests/panelOtbora.test.tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { expect, test, vi } from 'vitest'

import { poiskIliPusto } from '../src/domain/adres'
import { primenit, type OpisanieKolonki, type Sortirovka, type Vybor } from '../src/domain/tablitsa'
import { PanelOtbora } from '../src/ui/PanelOtbora'

type Stroka = { id: string; nazvanie: string; kategoriya: string; tsena: string | null }
const STROKI: Stroka[] = [
  { id: 'B002', nazvanie: 'Маргарита', kategoriya: 'Пицца', tsena: '450.00' },
  { id: 'B001', nazvanie: 'Кетчуп', kategoriya: 'Соус', tsena: '5.52' },
  { id: 'B003', nazvanie: 'Песто', kategoriya: 'Соус', tsena: null },
]
const KOLONKI: OpisanieKolonki<Stroka>[] = [
  { key: 'name', title: 'Название', sort: { vid: 'tekst', znachenie: (r) => r.nazvanie } },
  { key: 'category', title: 'Категория', filtr: { vid: 'znacheniya', znachenie: (r) => r.kategoriya } },
  {
    key: 'price',
    title: 'Цена',
    sort: { vid: 'chislo', znachenie: (r) => r.tsena },
    filtr: { vid: 'usloviya', usloviya: [{ kod: 'est', podpis: 'есть', podhodit: (r) => r.tsena !== null }, { kod: 'net', podpis: 'нет', podhodit: (r) => r.tsena === null }] },
  },
]

function Stend({ onSbrositVsyo = () => {} }: { onSbrositVsyo?: () => void }) {
  const knopka = useRef<HTMLButtonElement>(null)
  const [sortirovka, zadatSortirovku] = useState<Sortirovka | null>(null)
  const [vybor, zadatVybor] = useState<Vybor>({})
  const itog = primenit(STROKI, KOLONKI, { poisk: poiskIliPusto(''), sortirovka, vybor }, (r) => [r.nazvanie])
  return (
    <PanelOtbora
      knopka={knopka}
      kolonki={KOLONKI}
      naideno={itog.stroki.length}
      sortirovka={sortirovka}
      onSortirovka={zadatSortirovku}
      gruppy={itog.gruppy}
      onVybor={(kolonka, kody) => zadatVybor((p) => ({ ...p, [kolonka]: kody }))}
      onSbrositVsyo={() => {
        onSbrositVsyo()
        zadatVybor({})
      }}
    />
  )
}

const knopka = () => screen.getByRole('button', { name: /^Сортировка и фильтры/ })
const dialog = () => screen.getByRole('dialog', { name: 'Сортировка и фильтры' })

test('кнопка открывает панель снизу: сортировка переключателями, группы галочек, «Показать N»', async () => {
  render(<Stend />)
  expect(knopka()).toHaveAttribute('aria-haspopup', 'dialog')
  expect(knopka()).toHaveAttribute('aria-expanded', 'false')
  await userEvent.click(knopka())

  const d = dialog()
  expect(knopka()).toHaveAttribute('aria-expanded', 'true')
  expect(knopka()).toHaveAttribute('aria-controls', d.id)
  const sortirovka = within(d).getByRole('group', { name: 'Сортировка' })
  expect(within(sortirovka).getAllByRole('radio').map((r) => r.parentElement?.textContent)).toEqual([
    'Без сортировки',
    'Название: от А до Я',
    'Название: от Я до А',
    'Цена: по возрастанию',
    'Цена: по убыванию',
  ])
  expect(within(sortirovka).getByRole('radio', { name: 'Без сортировки' })).toBeChecked()
  expect(within(d).getByRole('group', { name: 'Категория' })).toBeInTheDocument()
  expect(within(d).getByRole('button', { name: 'Показать 3' })).toBeInTheDocument()
  expect(within(d).queryByRole('button', { name: 'Сбросить всё' })).not.toBeInTheDocument()
})

test('галочка применяется сразу: счётчик «Показать N» и число на кнопке живые; «Сбросить всё» не закрывает', async () => {
  const sbros = vi.fn()
  render(<Stend onSbrositVsyo={sbros} />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('checkbox', { name: 'Соус · 2' }))
  expect(within(dialog()).getByRole('button', { name: 'Показать 2' })).toBeInTheDocument()
  expect(knopka()).toHaveTextContent('Сортировка и фильтры (1)')

  await userEvent.click(within(dialog()).getByRole('button', { name: 'Сбросить всё' }))
  expect(sbros).toHaveBeenCalledTimes(1)
  expect(dialog()).toBeInTheDocument()
  expect(within(dialog()).getByRole('button', { name: 'Показать 3' })).toBeInTheDocument()
})

test('переключатель сортировки меняет сортировку; «Показать» закрывает и возвращает фокус на кнопку', async () => {
  render(<Stend />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('radio', { name: 'Цена: по убыванию' }))
  expect(within(dialog()).getByRole('radio', { name: 'Цена: по убыванию' })).toBeChecked()

  await userEvent.click(within(dialog()).getByRole('button', { name: 'Показать 3' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(knopka()).toHaveFocus()
  expect(knopka()).toHaveAttribute('aria-expanded', 'false')
})

test('включённая группа раскрыта, остальные свёрнуты', async () => {
  render(<Stend />)
  await userEvent.click(knopka())
  await userEvent.click(within(dialog()).getByRole('checkbox', { name: 'есть · 2' }))
  await userEvent.click(within(dialog()).getByRole('button', { name: 'Показать 2' }))

  await userEvent.click(knopka())
  const details = [...dialog().querySelectorAll('details')]
  expect(details.map((d) => [d.querySelector('summary')?.textContent, d.open])).toEqual([
    ['Категория', false],
    ['Цена · 1', true],
  ])
})
```

В `tests/panelTablitsy.test.tsx` тесты 360 px (ищущие `combobox` «Сортировка», кнопку «Фильтры», группу «Фильтры») заменить на:

```tsx
test('на 360 px: поле во всю строку, «Найдено», кнопка «Сортировка и фильтры» открывает панель', async () => {
  setViewport(360)
  render(<Stend />)
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Фильтры/ })).not.toBeInTheDocument()
  expect(screen.getByText('Найдено: 3 из 3')).toHaveAttribute('aria-live', 'polite')
  await userEvent.click(screen.getByRole('button', { name: 'Сортировка и фильтры' }))
  expect(screen.getByRole('dialog', { name: 'Сортировка и фильтры' })).toBeInTheDocument()
})

test('на 360 px число включённых групп — на кнопке; фишки под ней снимают фильтр', async () => {
  setViewport(360)
  render(<Stend vyborNachalo={{ category: ['Соус'], price: ['est'] }} />)
  expect(screen.getByRole('button', { name: 'Сортировка и фильтры (2)' })).toBeInTheDocument()
  await userEvent.click(fishka('Категория: Соус'))
  expect(screen.getByRole('button', { name: 'Сортировка и фильтры (1)' })).toBeInTheDocument()
})
```

Тесты про фокус после исчезнувшей фишки на 360 px («Фильтры» получает фокус — строки 284, 293): ожидать фокус на кнопке `/^Сортировка и фильтры/`. В `tests/tablitsaSFiltrami.test.tsx` (185–186, 233–234, 307–309), `tests/blyuda.test.tsx` (266, 282) и `tests/spravochnik.test.tsx` (229): открывать панель кнопкой «Сортировка и фильтры», группы искать `within(screen.getByRole('dialog', { name: 'Сортировка и фильтры' }))`, сортировку выбирать `userEvent.click(within(dialog).getByRole('radio', { name: 'Цена: по убыванию' }))` вместо `selectOptions`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/panelOtbora.test.tsx tests/panelTablitsy.test.tsx`
Expected: FAIL — нет `PanelOtbora`; на 360 px по-прежнему `combobox`.

- [ ] **Step 3: Write `PanelOtbora.tsx`**

```tsx
// frontend/src/ui/PanelOtbora.tsx
import { useCallback, useId, useMemo, useState, type RefObject } from 'react'

import { kodSortirovki, razobratKodSortirovki } from '../domain/adres'
import { variantySortirovki, type GruppaFiltra, type OpisanieKolonki, type Sortirovka } from '../domain/tablitsa'
import { Galochki } from './Galochki'
import { ModalnayaPanel } from './ModalnayaPanel'

type Props<T> = {
  /** Кнопка «Сортировка и фильтры» — у панели над таблицей: на неё уходит
      фокус с исчезнувших фишек, на неё же возвращается при закрытии. */
  knopka: RefObject<HTMLButtonElement>
  kolonki: readonly OpisanieKolonki<T>[]
  naideno: number
  sortirovka: Sortirovka | null
  onSortirovka: (s: Sortirovka | null) => void
  gruppy: readonly GruppaFiltra[]
  onVybor: (kolonka: string, kody: string[]) => void
  onSbrositVsyo: () => void
}

/**
 * Сортировка и фильтры на телефоне — панель снизу поверх списка, а не в
 * потоке страницы: 25 галочек стеной уводили список за экран. Отбор
 * применяется сразу (состояние — в адресе, как и на компьютере), «Показать
 * N» только закрывает; N живой.
 */
export function PanelOtbora<T>({ knopka, kolonki, naideno, sortirovka, onSortirovka, gruppy, onVybor, onSbrositVsyo }: Props<T>) {
  const [otkryta, otkryt] = useState(false)
  const id = useId()
  const punkty = useMemo(() => variantySortirovki(kolonki), [kolonki])
  const vklyucheno = gruppy.filter((g) => g.aktivna).length
  const zakryt = useCallback(() => otkryt(false), [])
  const tekushchiy = sortirovka ? kodSortirovki(sortirovka) : ''

  return (
    <>
      <button
        ref={knopka}
        type="button"
        className="panel-otbora-knopka"
        aria-haspopup="dialog"
        aria-expanded={otkryta}
        aria-controls={otkryta ? id : undefined}
        onClick={() => otkryt(!otkryta)}
      >
        {vklyucheno > 0 ? `Сортировка и фильтры (${vklyucheno})` : 'Сортировка и фильтры'}
      </button>
      {otkryta && (
        <ModalnayaPanel
          id={id}
          storona="snizu"
          nazvanie="Сортировка и фильтры"
          onZakryt={zakryt}
          otkryvatel={knopka}
          niz={
            <>
              <button type="button" className="panel-otbora-pokazat" onClick={zakryt}>
                {`Показать ${naideno}`}
              </button>
              {vklyucheno > 0 && (
                <button type="button" className="panel-otbora-sbros" onClick={onSbrositVsyo}>
                  Сбросить всё
                </button>
              )}
            </>
          }
        >
          <fieldset className="panel-otbora-sortirovka">
            <legend>Сортировка</legend>
            <label className="panel-otbora-variant">
              <input type="radio" name={`${id}-sort`} value="" checked={tekushchiy === ''} onChange={() => onSortirovka(null)} />
              <span>Без сортировки</span>
            </label>
            {punkty.map((p) => (
              <label key={p.kod} className="panel-otbora-variant">
                <input
                  type="radio"
                  name={`${id}-sort`}
                  value={p.kod}
                  checked={tekushchiy === p.kod}
                  onChange={() => onSortirovka(razobratKodSortirovki(p.kod))}
                />
                <span>{p.podpis}</span>
              </label>
            ))}
          </fieldset>
          {gruppy.map((g) => (
            <GruppaOtbora key={g.kolonka} gruppa={g} onVybor={onVybor} />
          ))}
        </ModalnayaPanel>
      )}
    </>
  )
}

/** Группа галочек в сворачиваемом блоке: включённая при открытии раскрыта.
    Своё состояние, а не `open={aktivna}`: иначе свёрнутую руками группу
    перерисовка после каждой галочки раскрывала бы снова. */
function GruppaOtbora({ gruppa, onVybor }: { gruppa: GruppaFiltra; onVybor: (kolonka: string, kody: string[]) => void }) {
  const [raskryta, raskryt] = useState(gruppa.aktivna)
  const vybrano = gruppa.varianty.filter((v) => v.vybran).length
  return (
    <details className="panel-otbora-gruppa" open={raskryta} onToggle={(event) => raskryt(event.currentTarget.open)}>
      <summary>{vybrano > 0 ? `${gruppa.zagolovok} · ${vybrano}` : gruppa.zagolovok}</summary>
      <Galochki gruppa={gruppa} onVybor={onVybor} />
    </details>
  )
}
```

- [ ] **Step 4: Narrow branch of `PanelTablitsy.tsx`**

Удалить `UzkoeUpravlenie` и `SvoystvaUzkogo`; вместо них в `PanelTablitsy`:

```tsx
  return (
    <div className="panel-tablitsy">
      <div className="panel-tablitsy-ryad">
        <input ... как сейчас ... />
        {wide && naidenoEl}
      </div>
      {!wide && (
        <div className="panel-tablitsy-ryad panel-tablitsy-ryad--otbor">
          {naidenoEl}
          <PanelOtbora
            knopka={knopkaFiltrov}
            kolonki={kolonki}
            naideno={naideno}
            sortirovka={sortirovka}
            onSortirovka={onSortirovka}
            gruppy={gruppy}
            onVybor={onVybor}
            onSbrositVsyo={onSbrositVsyo}
          />
        </div>
      )}
      {estOtbor && ( ...фишки и «Сбросить всё» как сейчас... )}
    </div>
  )
```

где `const naidenoEl = <span className="panel-tablitsy-naideno" aria-live="polite" aria-atomic="true">{`Найдено: ${naideno} из ${vsego}`}</span>` — один и тот же узел живёт в одном месте DOM в каждой ветке (широкая — рядом с полем, узкая — под полем). Импорты `kodSortirovki`, `razobratKodSortirovki`, `variantySortirovki`, `Galochki`, `useId`, `useMemo`, `KeyboardEvent` из `PanelTablitsy.tsx` убрать, если больше не нужны.

- [ ] **Step 5: Styles** (добавить в `panelTablitsy.css`; блок `.panel-tablitsy-panel`, `.panel-tablitsy-niz`, `.panel-tablitsy-gotovo`, `.panel-tablitsy-sortirovka`, `.panel-tablitsy-filtry` — удалить)

```css
/* На телефоне: поле во всю строку, под ним «Найдено» и кнопка отбора. */
.panel-tablitsy-ryad--otbor {
  justify-content: space-between;
}

.panel-otbora-knopka {
  min-height: 44px;
  padding: var(--shag) calc(var(--shag) * 3);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: var(--poverhnost);
  color: var(--tekst);
  font: inherit;
  cursor: pointer;
}

.panel-otbora-knopka[aria-expanded='true'] {
  border-color: var(--aktsent);
  color: var(--aktsent);
}

.panel-otbora-sortirovka {
  margin: 0 0 calc(var(--shag) * 3);
  padding: 0;
  border: 0;
}

.panel-otbora-sortirovka legend {
  padding: 0;
  margin-bottom: var(--shag);
  font-weight: 600;
  color: var(--priglushyonnyy);
}

.panel-otbora-variant {
  display: flex;
  align-items: center;
  gap: calc(var(--shag) * 2);
  min-height: 44px;
  cursor: pointer;
}

.panel-otbora-variant input {
  margin: 0;
  accent-color: var(--aktsent);
}

.panel-otbora-gruppa {
  border-top: 1px solid var(--granitsa);
}

.panel-otbora-gruppa summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  font-weight: 600;
  cursor: pointer;
}

/* Заголовок группы уже в summary — легенду галочек прячем визуально,
   оставляя программе чтения с экрана. */
.panel-otbora-gruppa .galochki legend {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}

.panel-otbora-pokazat {
  flex: 1;
  min-height: 44px;
  border: 0;
  border-radius: var(--radius);
  background: var(--aktsent);
  color: var(--poverhnost);
  font: inherit;
  cursor: pointer;
}

.panel-otbora-sbros {
  min-height: 44px;
  padding: 0 calc(var(--shag) * 3);
  border: 0;
  background: none;
  color: var(--aktsent);
  font: inherit;
  cursor: pointer;
}
```

В том же файле поле поиска на телефоне — во всю ширину: в блок `@media (pointer: coarse)` ничего не добавлять; вместо этого `.panel-tablitsy-poisk { flex: 1 1 200px; max-width: 480px; }` заменить на `.panel-tablitsy-poisk { flex: 1 1 200px; } .obolochka:not(.obolochka--telefon) .panel-tablitsy-poisk { max-width: 480px; }`.

- [ ] **Step 6: Run everything, commit**

Run: `cd frontend && npm run types && npm run lint && npm run test`
Expected: PASS, включая переписанные тесты 360 px.

```bash
git add frontend/src/ui/PanelOtbora.tsx frontend/src/ui/PanelTablitsy.tsx frontend/src/ui/panelTablitsy.css frontend/tests/panelOtbora.test.tsx frontend/tests/panelTablitsy.test.tsx frontend/tests/tablitsaSFiltrami.test.tsx frontend/tests/blyuda.test.tsx frontend/tests/spravochnik.test.tsx
git commit -m "Сортировка и фильтры на телефоне: панель снизу с «Показать N»"
```

---

### Task 13: Итог PR-2 — снимки, ревью, PR

- [ ] **Step 1:** `cd frontend && npm run types && npm run lint && npm run test && npm run build`.
- [ ] **Step 2:** `cd backend && VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py` — код 0: строк в блюдах ≥ 8, целей меньше 44 px нет; посмотреть `tel360-02-blyuda.png`, `tel360-04-otbor.png`, `tel360-06-spravochnik.png`, `pk-02-blyuda.png` (компьютер без изменений, кроме наведения на красной строке).
- [ ] **Step 3:** ревью ветки агентом на модели чата по спеке 2.6, 2.7, 2.12; `git diff main --stat` без файлов этапа 5.
- [ ] **Step 4:** PR «Мобильный вид, часть 2: строки списков и панель отбора» со снимками; слияние — по слову Александра.

---
## PR-3 «Карточка блюда» — задачи 14–16

### Task 14: Показатели 2×2, «← Блюда» под палец, поля входа 16 px

**Files:**
- Modify: `frontend/src/pages/pages.css`, `frontend/src/auth/auth.css`
- Test: `frontend/tests/kartochka.test.tsx` (проверка класса — CSS jsdom не считает; сама раскладка проверяется снимком в задаче 16)

- [ ] **Step 1: Add styles**

В `pages.css`:

```css
/* На телефоне четыре показателя — сеткой 2×2, а не в ряд с переносом «3 + 1». */
.obolochka--telefon .krupno {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: calc(var(--shag) * 4) calc(var(--shag) * 6);
}

/* «← Блюда» — цель под палец, не мельче 44 px; текст остаётся ссылкой. */
@media (pointer: coarse) {
  .nazad {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding-right: calc(var(--shag) * 3);
  }
}
```

В `auth.css`:

```css
/* Поле мельче 16 px iOS Safari при фокусе приближает, и страница уезжает. */
@media (pointer: coarse) {
  .vhod-forma input {
    font-size: 16px;
  }
}
```

- [ ] **Step 2: Check and commit**

Run: `cd frontend && npm run lint && npm run test`

```bash
git add frontend/src/pages/pages.css frontend/src/auth/auth.css
git commit -m "Карточка блюда и вход на телефоне: показатели 2×2, цели 44 px, поля 16 px"
```

---

### Task 15: Состав блюда списком на телефоне

**Files:**
- Modify: `frontend/src/pages/DishDetail.tsx` (функция `Sostav` — ветка по `useWide`), `frontend/src/pages/pages.css`
- Test: `frontend/tests/kartochka.test.tsx` (добавить тесты 360 px)

**Interfaces:**
- Consumes: `useWide` из `ui/useWide.ts`, `Num`.
- Produces: на телефоне `Sostav` рисует `<ul class="sostav-spisok">` с позициями: имя (короткое + полное), `dl` с подписями «нетто», «брутто» (только не у упаковки), «цена», «стоимость», «доля»; упаковка — своя группа с заголовком `h3` «Упаковка». На компьютере — прежняя таблица без изменений.

- [ ] **Step 1: Write the failing tests** (в `tests/kartochka.test.tsx`; `setViewport` импортировать из `./setup`)

```tsx
test('на 360 px состав — список с подписанными числами, без таблицы', async () => {
  setViewport(360)
  narisovat()
  await screen.findByText('Салат айсберг пф')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()

  const pozitsii = screen.getAllByRole('listitem')
  const aysberg = pozitsii.find((li) => li.textContent?.includes('Салат айсберг пф'))!
  const chisla = [...aysberg.querySelectorAll('dt')].map((dt) => dt.textContent)
  expect(chisla).toEqual(['нетто', 'брутто', 'цена', 'стоимость', 'доля'])
  expect(aysberg).toHaveTextContent('16 г')
  expect(aysberg).toHaveTextContent('22,19 г')
  expect(aysberg).toHaveTextContent('213,38 ₽/кг')
  expect(aysberg).toHaveTextContent('4,74 ₽')
  expect(aysberg).toHaveTextContent('5,6 %')
  // Полное имя — под коротким.
  expect(within(aysberg).getByText('Салат айсберг')).toHaveClass('polnoe-imya')
})

test('на 360 px упаковка — своя группа: заголовок, штуки, без брутто', async () => {
  setViewport(360)
  narisovat()
  await screen.findByText('Салат айсберг пф')
  expect(screen.getByRole('heading', { level: 3, name: /Упаковка/ })).toBeInTheDocument()
  const konteyner = screen.getAllByRole('listitem').find((li) => li.textContent?.includes('Контейнер'))!
  expect([...konteyner.querySelectorAll('dt')].map((dt) => dt.textContent)).toEqual(['нетто', 'цена', 'стоимость', 'доля'])
  expect(konteyner).toHaveTextContent('1 шт')
})

test('на 1440 px состав по-прежнему таблица', async () => {
  setViewport(1440)
  narisovat()
  await screen.findByText('Салат айсберг пф')
  expect(screen.getByRole('table')).toBeInTheDocument()
  expect(screen.queryByRole('list')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/kartochka.test.tsx`
Expected: FAIL — на 360 px по-прежнему таблица.

- [ ] **Step 3: Implement**

В `DishDetail.tsx` добавить `import { useWide } from '../ui/useWide'` и заменить `Sostav`:

```tsx
function Sostav({ osnova, upakovka }: { osnova: Component[]; upakovka: Component[] }) {
  const wide = useWide()
  if (!wide) return <SostavSpiskom osnova={osnova} upakovka={upakovka} />
  return (
    ... прежняя таблица без изменений ...
  )
}

/**
 * Состав на телефоне — список: таблица из шести колонок на 360 px
 * прокручивалась внутри рамки, и стоимость с долей были видны только после
 * сдвига вбок (ТЗ, раздел 2: на узком экране — список). Упаковка — своя
 * группа: в выход блюда не входит, брутто у неё нет.
 */
function SostavSpiskom({ osnova, upakovka }: { osnova: Component[]; upakovka: Component[] }) {
  return (
    <div className="sostav-spisok">
      <ul className="sostav-gruppa">
        {osnova.map((k, nomer) => (
          <PozitsiyaSostava key={`osnova-${nomer}`} k={k} />
        ))}
      </ul>
      {upakovka.length > 0 && (
        <>
          <h3 className="sostav-zagolovok">
            Упаковка <span className="sostav-poyasnenie">· в выход блюда не входит, брутто нет</span>
          </h3>
          <ul className="sostav-gruppa">
            {upakovka.map((k, nomer) => (
              <PozitsiyaSostava key={`upakovka-${nomer}`} k={k} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function PozitsiyaSostava({ k }: { k: Component }) {
  const upakovka = k.row_type === 'packaging'
  const edinitsa = upakovka ? 'шт' : 'г'
  return (
    <li className="pozitsiya">
      <span className="pozitsiya-imya imya-ingredienta">
        {k.short_name || k.name}
        {k.short_name && <span className="polnoe-imya">{k.name}</span>}
      </span>
      <dl className="pozitsiya-chisla">
        <div>
          <dt>нетто</dt>
          <dd><Num value={k.net_weight_g} unit={edinitsa} /></dd>
        </div>
        {!upakovka && (
          <div>
            <dt>брутто</dt>
            <dd><Num value={k.gross_weight_g} unit="г" /></dd>
          </div>
        )}
        <div>
          <dt>цена</dt>
          <dd><Num value={k.price_per_unit} fraction={2} unit={`₽/${k.unit}`} /></dd>
        </div>
        <div>
          <dt>стоимость</dt>
          <dd><Num value={k.cost_rub} fraction={2} unit="₽" /></dd>
        </div>
        <div>
          <dt>доля</dt>
          <dd><Num value={k.share_percent} fraction={1} unit="%" /></dd>
        </div>
      </dl>
    </li>
  )
}
```

Стили в `pages.css`:

```css
/* Состав списком на телефоне. */
.sostav-gruppa {
  list-style: none;
  margin: 0;
  padding: 0;
  background: var(--poverhnost);
}

.pozitsiya {
  padding: calc(var(--shag) * 2) calc(var(--shag) * 3);
  border-bottom: 1px solid var(--granitsa);
}

.pozitsiya-imya {
  display: block;
  margin-bottom: var(--shag);
}

/* Числа — плиткой по три в ряд: подпись сверху мелким, значение снизу. */
.pozitsiya-chisla {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--shag) calc(var(--shag) * 2);
  margin: 0;
}

.pozitsiya-chisla dt {
  color: var(--priglushyonnyy);
  font-size: 11px;
}

.pozitsiya-chisla dd {
  margin: 0;
}

.sostav-zagolovok {
  margin: calc(var(--shag) * 4) 0 calc(var(--shag) * 2);
  font-size: 14px;
}

.sostav-poyasnenie {
  font-weight: 400;
  color: var(--priglushyonnyy);
}
```

- [ ] **Step 4: Run, commit**

Run: `cd frontend && npm run types && npm run lint && npm run test`

```bash
git add frontend/src/pages/DishDetail.tsx frontend/src/pages/pages.css frontend/tests/kartochka.test.tsx
git commit -m "Состав блюда на телефоне — списком с подписанными числами"
```

---

### Task 16: Итог PR-3 — снимки, документы, ревью, PR

- [ ] **Step 1:** полный набор проверок и `npm run build`.
- [ ] **Step 2:** `cd backend && VIRTUAL_ENV= uv run python ../frontend/e2e/snimki.py` — код 0; посмотреть `tel360-05-kartochka.png` (состав без сдвига вбок, показатели 2×2), `pk-05-kartochka.png` (как прежде).
- [ ] **Step 3: ROADMAP.** В `docs/ROADMAP.md` добавить в конец раздела «Где мы сейчас» подраздел «Этап 6 — мобильный вид» с тремя PR и строками доказательств (номера PR, число тестов, результат `snimki.py`, приёмка Александром — после неё). Пункт «Узкий вид состава в карточке блюда» в «Долги и открытые решения» — пометить «закрыт этапом 6 (PR-3)»; строку про «Цели касания» (Ф-3) — так же. Правки ROADMAP делать одним коммитом в самом конце, чтобы не конфликтовать с этапом 5.
- [ ] **Step 4:** ревью ветки агентом на модели чата по спеке 2.8, 2.11; PR «Мобильный вид, часть 3: карточка блюда». Живая приёмка с Александром на телефоне по чек-листу спеки (раздел 6, п. 3). Слияние — по его слову.

---

## Риски

- **iOS Safari**: `inert` с 15.5, `100dvh` с 15.4; запрет прокрутки через `position: fixed` на body возвращает прокрутку при закрытии; закреплённые вкладки над клавиатурой — прячутся при фокусе в поле. Проверяется только на живом iPhone.
- **Две ссылки «Блюда» на телефоне** (вкладки и открытая шторка): тесты ищут через `within`.
- **Смена `priority` → `uzkiy`** трогает описания колонок и тесты `tablitsa`, `tablitsaSFiltrami`, `zagolovok`, `blyuda`, `spravochnik`; компьютер не меняется — снимок 1440 px и прежние тесты таблицы.
- **Порядок подписи справочника** — порядок колонок (`id · категория · ед.`), не менять порядок колонок таблицы ради подписи.
- **Конфликт с этапом 5** — `tests/obolochka.test.tsx` (их тест меню повара) и одна строка в `Vkladki.tsx` (фильтр по ролям, меняют они на шаге 9).
- **`<details>` и React**: состояние раскрытия — своё, `onToggle`; `open={aktivna}` раскрывало бы свёрнутую группу после каждой галочки.
- **Манифест**: файлы `public/` попадают в корень `dist`, не в `/assets/` — заголовка кэша нет; при смене иконок сменить и имена файлов.

## Проверка

1. Каждая задача: тесты сначала красные; `npm run types && npm run lint && npm run test` зелёные; коммит.
2. Перед каждым PR: `npm run build`; `e2e/snimki.py` — код 0 (после PR-2), снимки в описании PR; `git diff main --stat` без файлов этапа 5; ревью агентом на модели чата по спеке.
3. Снимок 1440 px до и после этапа — без различий, кроме наведения на красной строке.
4. Приёмка Александром на его телефоне и на iPhone: шторка, затемнение, Escape, вкладки, строки, панель снизу, состав, «Добавить на главный экран».
5. ROADMAP: этап 6 — со строками доказательств.

## Поправки по ходу исполнения

(заполняется исполнителем: что отклонилось от плана и почему)
