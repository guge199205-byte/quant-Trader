import { Route, Routes } from 'react-router-dom';
import Navbar from './components/Navbar';
import Home from './pages/Home';
import Live from './pages/Live';
import Leaderboard from './pages/Leaderboard';
import Models from './pages/Models';
import Control from './pages/Control';
import TradingSettings from './pages/TradingSettings';
import DataPlatform from './pages/DataPlatform';
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
        <Route path="/models" element={<Models />} />
        <Route path="/control" element={<Control />} />
        <Route path="/trading" element={<TradingSettings />} />
        <Route path="/data-platform" element={<DataPlatform />} />
        <Route path="/model/:market/:agent" element={<ModelDetail />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </>
  );
}
