import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: { proxy: {
    '/v1': 'http://localhost:8090', '/admin': 'http://localhost:8090',
    '/events': 'http://localhost:8090', '/health': 'http://localhost:8090'
  }},
  build: { outDir: 'dist' }
})
