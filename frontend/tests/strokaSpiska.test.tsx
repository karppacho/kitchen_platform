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
