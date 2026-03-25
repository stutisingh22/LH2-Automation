import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Override if needed: LH2_API_PROXY=http://127.0.0.1:PORT npm run dev
const apiProxy = process.env.LH2_API_PROXY || 'http://127.0.0.1:10001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiProxy,
        changeOrigin: true,
      },
    },
  },
})
