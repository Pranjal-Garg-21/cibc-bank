/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// The API and the app share one origin in production (FastAPI serves dist/). In development Vite proxies /api,
// and the API accepts this dev origin only with UI_DEV=1.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  build: {
    sourcemap: false, // no source maps in the shipped build (design §9.7)
    assetsInlineLimit: 0, // fonts and images as files, so the CSP needs no data: fonts
    modulePreload: { polyfill: false }, // no inline polyfill script
  },
  test: { environment: 'node' },
})
