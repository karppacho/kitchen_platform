import { useState, type FormEvent } from 'react'

import { ApiError } from '../api/client'
import './auth.css'
import { useSession } from './session'

/**
 * Почта, пароль, кнопка.
 *
 * Ссылок «зарегистрироваться» и «забыли пароль» нет: доступ заводит
 * администратор скриптом grant_access.py, пароль сбрасывает он же.
 */
export function LoginPage() {
  const { login } = useSession()
  const [email, setEmail] = useState('')
  const [parol, setParol] = useState('')
  const [oshibka, setOshibka] = useState<string | null>(null)
  const [idyot, setIdyot] = useState(false)

  async function otpravit(event: FormEvent) {
    event.preventDefault()
    setOshibka(null)
    setIdyot(true)
    try {
      await login(email, parol)
    } catch (prichina: unknown) {
      setOshibka(prichina instanceof ApiError ? prichina.message : 'Не удалось войти')
    } finally {
      setIdyot(false)
    }
  }

  return (
    <main className="vhod">
      <form className="vhod-forma" onSubmit={otpravit}>
        <h1>Кухня</h1>
        <label htmlFor="pochta">Почта</label>
        <input
          id="pochta"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="parol">Пароль</label>
        <input
          id="parol"
          type="password"
          autoComplete="current-password"
          value={parol}
          onChange={(e) => setParol(e.target.value)}
          required
        />
        {/* Ошибку показываем текстом у формы, не всплывашкой: всплывашка
            уезжает раньше, чем человек успевает прочитать. */}
        {oshibka && (
          <p className="vhod-oshibka" role="alert">
            {oshibka}
          </p>
        )}
        <button type="submit" disabled={idyot}>
          Войти
        </button>
      </form>
    </main>
  )
}
