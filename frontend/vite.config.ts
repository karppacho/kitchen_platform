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
  },
})
