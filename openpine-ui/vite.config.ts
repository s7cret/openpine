import { configDefaults, defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import vueI18n from '@intlify/unplugin-vue-i18n/vite'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    vueI18n({ include: path.resolve(__dirname, './src/i18n/locales/**') }),
  ],
  test: {
    exclude: [...configDefaults.exclude, 'tests/**/*.test.mjs', 'e2e/**'],
    coverage: {
      provider: 'v8',
      include: ['src/api/**/*.ts', 'src/lib/**/*.ts', 'src/stores/{backtests,pineFiles,strategies}.ts'],
      exclude: ['**/*.test.ts'],
      reporter: ['text', 'json-summary'],
      thresholds: {
        statements: 60,
        branches: 45,
        functions: 50,
        lines: 70,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 1888,
    host: '0.0.0.0',
    proxy: {
      '/health': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
  css: {
    postcss: {
      plugins: [tailwindcss(), autoprefixer()],
    },
  },
})
