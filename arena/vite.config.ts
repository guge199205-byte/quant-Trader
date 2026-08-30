import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // dev 模式直连宿主机 api（生产走 nginx 同源代理，浏览器不需要 token）
      '/api': 'http://127.0.0.1:8091',
    },
  },
  build: {
    chunkSizeWarningLimit: 900,
  },
});
