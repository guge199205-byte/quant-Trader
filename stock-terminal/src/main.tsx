import React from 'react';
import { createRoot } from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { HashRouter } from 'react-router-dom';
import './styles/global.css';
import LoginGate from './LoginGate';
import StockTerminalPage from './app/features/stock-terminal/pages/StockTerminalPage';

document.title = '老版个股终端 · 本地独立版';

createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN}>
      <AntApp className="h-full">
        <HashRouter>
          <LoginGate>
            <StockTerminalPage />
          </LoginGate>
        </HashRouter>
      </AntApp>
    </ConfigProvider>,
);