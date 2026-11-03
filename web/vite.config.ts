import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Gọi /api/* sẽ được chuyển tới backend FastAPI (uvicorn app.main:app --port 8000)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
