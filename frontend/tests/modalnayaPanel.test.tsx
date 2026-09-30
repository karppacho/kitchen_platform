import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { expect, test } from 'vitest'

import { ModalnayaPanel } from '../src/ui/ModalnayaPanel'

// Тип назван явно: с `'sleva' as const` в значении по умолчанию TypeScript
// вывел бы только `'sleva'`, и стенд со стороной «снизу» не собрался бы.
function Stend({ storona = 'sleva' }: { storona?: 'sleva' | 'snizu' }) {
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
