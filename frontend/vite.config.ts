import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

const backend = { target: 'http://127.0.0.1:8000', changeOrigin: false }

export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { '/api': backend, '/media': backend } },
  build: { outDir: '../backend/src/photobooth/web', emptyOutDir: true },
})
