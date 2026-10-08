import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 老版个股终端独立应用：http://localhost:8099
// 前端全部 API 走同源 /api + /health，经本代理转发到本机后端网关 8000（quantmind 容器）。
// WebSocket（/api/v1/ws/*）同样代理，见 ws: true。
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 局域网可访问（0.0.0.0）
    port: 8099,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});