import { Route, Routes } from 'react-router-dom';
import Navbar from './components/Navbar';
import Home from './pages/Home';
import Live from './pages/Live';
import Leaderboard from './pages/Leaderboard';
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
        <Route path="/model/:market/:agent" element={<ModelDetail />} />
        <Route path="/about" element={<About />} />
      </Routes>
    </>
  );
}
