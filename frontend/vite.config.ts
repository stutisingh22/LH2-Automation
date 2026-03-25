import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 👇 ADD THIS LINE (keep your existing env if any)
const apiProxy = 'http://127.0.0.1:8001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    // Allow nginx-hosted domain access through reverse proxy.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: apiProxy,
        changeOrigin: true,
      },
    },
  },
})
