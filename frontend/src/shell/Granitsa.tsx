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
