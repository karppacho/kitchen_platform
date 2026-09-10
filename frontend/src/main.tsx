import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import './styles/base.css'

const queries = new QueryClient({
  defaultOptions: {
    queries: {
      // Справочник и блюда меняются не чаще, чем шеф правит таблицу.
      staleTime: 60_000,
      // Повторять запрос, отвергнутый по правам, бессмысленно и вредно:
      // это выглядит как зависание.
      retry: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
