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
  const { login, logout, vyhodNePodtverzhden } = useSession()
  const [email, setEmail] = useState('')
  const [parol, setParol] = useState('')
  const [oshibka, setOshibka] = useState<string | null>(null)
  const [idyot, setIdyot] = useState(false)
  const [vyhodit, setVyhodit] = useState(false)

  async function povtoritVyhod() {
    setVyhodit(true)
    try {
      // logout сам не бросает: исход — в vyhodNePodtverzhden.
      await logout()
    } finally {
      setVyhodit(false)
    }
  }

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
        {/* Экран очищен, но сервер выход не подтвердил: куки могут быть
            живы, и после перезагрузки планшета следующий человек окажется
            в чужой сессии. role="status", а не "alert": ошибка входа ниже
            остаётся единственным alert формы. */}
        {vyhodNePodtverzhden && (
          <div className="vhod-preduprezhdenie" role="status">
            <p>Выход не подтверждён сервером — сессия может быть ещё активна</p>
            <button type="button" onClick={() => void povtoritVyhod()} disabled={vyhodit}>
              Повторить выход
            </button>
          </div>
        )}
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
