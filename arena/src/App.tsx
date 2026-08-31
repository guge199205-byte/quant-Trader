import { Navigate, Route, Routes } from 'react-router-dom';
import Navbar from './components/Navbar';
import Home from './pages/Home';
import Live from './pages/Live';
import Leaderboard from './pages/Leaderboard';
import Control from './pages/Control';
import DataPlatform from './pages/DataPlatform';
import Harness from './pages/Harness';
import ModelDetail from './pages/ModelDetail';
import About from './pages/About';

export default function App() {
  return (
    <>
      <Navbar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/live" element={<Live />} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        {/* 原"模型"页已融合进"模型排行榜"（/leaderboard），旧链接重定向 */}
        <Route path="/models" element={<Navigate to="/leaderboard" replace />} />
        <Route path="/control" element={<Control />} />
        {/* 交易所设置已并入总控页 tab（/control?view=exchange），旧链接重定向 */}
        <Route path="/trading" element={<Navigate to="/control?view=exchange" replace />} />
        <Route path="/data-platform" element={<DataPlatform />} />
        <Route path="/harness" element={<Harness />} />
        <Route path="/model/:market/:agent" element={<ModelDetail />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </>
  );
}
