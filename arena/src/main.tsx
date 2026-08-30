import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/globals.css';

// 顶部时钟
const tick = () => {
  const el = document.getElementById('arena-clock');
  if (el) el.textContent = new Date().toLocaleString('zh-CN', { hour12: false });
};
tick();
setInterval(tick, 1000);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
