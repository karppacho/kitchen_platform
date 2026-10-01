import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'

import { dostupnye, RAZDELY } from '../src/shell/razdely'
import { razdelyVkladok, Vkladki } from '../src/shell/Vkladki'

const NAVIGATSIYA = { name: 'Основные разделы' }

// Стенд — шеф: ему открыты все разделы, и вкладки есть. Повар и человек
// без ролей — отдельными тестами ниже.
function narisovat(putj = '/dishes', roli: readonly string[] = ['chef']) {
  return render(
    <MemoryRouter initialEntries={[putj]}>
      <main>
        <input aria-label="Поиск" />
        <button type="button">Кнопка</button>
      </main>
      <Vkladki roli={roli} />
    </MemoryRouter>,
  )
}

test('вкладки — живые разделы из того же списка, без «скоро»', () => {
  const zhivye = RAZDELY.filter((r) => !r.faza)
  expect(razdelyVkladok()).toEqual(zhivye)
  expect(zhivye.map((r) => r.nazvanie)).toEqual([
    'Справочник',
    'Блюда',
    'Сверка справочника',
    'Новый ингредиент',
  ])
  // Функция принимает и чужой список: вкладки передают ей отобранный по
  // ролям — у коммерсанта «Нового ингредиента» нет ни в меню, ни внизу.
  expect(razdelyVkladok(dostupnye(RAZDELY, ['commerce'])).map((r) => r.put)).toEqual([
    '/ingredients',
    '/dishes',
    '/reconciliation',
  ])
  expect(
    razdelyVkladok([
      { put: '/x', nazvanie: 'Икс', roli: ['chef'] },
      { put: '/y', nazvanie: 'Игрек', roli: ['chef'], faza: 'фаза 9' },
    ]),
  ).toEqual([{ put: '/x', nazvanie: 'Икс', roli: ['chef'] }])
})

test('у шефа четыре вкладки; «Новый ингредиент» укорочен, но доступное имя полное', () => {
  narisovat('/cards')
  const nav = screen.getByRole('navigation', NAVIGATSIYA)
  // Четыре вкладки делят 360 px — длинные имена укорочены, порядок — как в меню.
  expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual([
    'Справочник',
    'Блюда',
    'Сверка',
    'Ингредиент',
  ])
  const ingredient = within(nav).getByRole('link', { name: 'Новый ингредиент' })
  expect(ingredient).toHaveAttribute('aria-label', 'Новый ингредиент')
  expect(ingredient).toHaveAttribute('aria-current', 'page')
})

test('у повара один раздел — вкладок нет вовсе, а не одна', () => {
  // Одна вкладка бессмысленна: вести некуда. Полосы внизу нет, и экран
  // карточки получает всю высоту телефона.
  narisovat('/cards', ['cook'])
  expect(screen.queryByRole('navigation', NAVIGATSIYA)).not.toBeInTheDocument()
})

test('без единой роли вкладок нет', () => {
  narisovat('/dishes', [])
  expect(screen.queryByRole('navigation', NAVIGATSIYA)).not.toBeInTheDocument()
})

test('текущая вкладка помечена aria-current, длинное имя укорочено, но доступное имя полное', () => {
  narisovat('/reconciliation')
  const nav = screen.getByRole('navigation', NAVIGATSIYA)
  const sverka = within(nav).getByRole('link', { name: 'Сверка справочника' })
  expect(sverka).toHaveAttribute('aria-current', 'page')
  expect(sverka).toHaveTextContent('Сверка')
  expect(within(nav).getByRole('link', { name: 'Блюда' })).not.toHaveAttribute('aria-current')
})

test('вложенный адрес держит вкладку раздела текущей', () => {
  narisovat('/dishes/B001')
  const nav = screen.getByRole('navigation', NAVIGATSIYA)
  expect(within(nav).getByRole('link', { name: 'Блюда' })).toHaveAttribute('aria-current', 'page')
})

test('пока фокус в поле ввода, вкладки спрятаны; ушёл — вернулись', async () => {
  narisovat()
  const nav = screen.getByRole('navigation', NAVIGATSIYA)
  await userEvent.click(screen.getByRole('textbox', { name: 'Поиск' }))
  expect(nav).toHaveAttribute('hidden')
  await userEvent.click(screen.getByRole('button', { name: 'Кнопка' }))
  expect(nav).not.toHaveAttribute('hidden')
})
