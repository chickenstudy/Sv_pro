import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
    server: {
    port: 3000,
    // Proxy API requests đến FastAPI backend (tránh CORS khi dev)
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Proxy go2rtc API để lấy stream info
      '/go2rtc-api': {
        target: 'http://localhost:1984',
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/go2rtc-api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Code splitting: vendor chunk riêng để cache lâu hơn
        manualChunks: {
          react: ['react', 'react-dom'],
        },
      },
    },
  },
})
