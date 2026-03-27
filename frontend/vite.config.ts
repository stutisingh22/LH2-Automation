import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// frontend/.env overrides:
//   VITE_DEV_PORT=3002          (default 3002)
//   VITE_DEV_API_PROXY=http://127.0.0.1:8000   (must match uvicorn port)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxy =
    (env.VITE_DEV_API_PROXY || '').trim() || 'http://127.0.0.1:8000'
  const devPort = Number.parseInt(env.VITE_DEV_PORT || '3002', 10) || 3002

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: devPort,
      // Allow nginx-hosted domain access through reverse proxy.
      allowedHosts: true,
      proxy: {
        '/api': {
          target: apiProxy,
          changeOrigin: true,
        },
      },
    },
  }
})
