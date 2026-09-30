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
