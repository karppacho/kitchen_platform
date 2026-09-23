import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// defineConfig берётся из vitest/config, а не из vite: иначе поле test
// не проходит проверку типов.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // В разработке фронт и API — разные порты. Прокси делает их одним
    // происхождением, иначе SameSite=Strict не отдаст куки.
    proxy: { '/api': { target: 'http://127.0.0.1:8080' } },
  },
  build: { outDir: 'dist' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
    // msw при загрузке трогает глобальный localStorage — в Node 22+ это
    // экспериментальная веб-фича, и Node печатает ExperimentalWarning на
    // каждый прогон. Функция не используется: msw читает её ради очистки
    // кук в jsdom, не в node. Гасим точечно только эту фичу флагом Node, а
    // не предупреждения вообще — остальные ExperimentalWarning остаются
    // видимыми.
    poolOptions: { forks: { execArgv: ['--no-experimental-webstorage'] } },
  },
})
