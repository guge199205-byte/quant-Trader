import { Link, NavLink } from 'react-router-dom';
import { MarketId } from '../api/client';
import './Navbar.css';

/** 终端风导航：黑 2px 底边 + 居中菜单 + 右侧外链 */
const MARKET_LABELS: Record<MarketId, string> = {
  us: '🇺🇸 美股',
  cn: '🇨🇳 A股',
  hk: '🇭🇰 港股',
};

export function MarketSwitcher({
  market,
  onChange,
}: {
  market: MarketId;
  onChange: (m: MarketId) => void;
}) {
  return (
    <div className="market-switcher" style={{ display: 'flex', gap: 0 }}>
      {(['cn', 'hk', 'us'] as MarketId[]).map((m) => (
        <button
          key={m}
          className={`chip ${m} ${market === m ? 'active' : ''}`}
          style={{ borderRadius: 0 }}
          onClick={() => onChange(m)}
        >
          {MARKET_LABELS[m]}
        </button>
      ))}
    </div>
  );
}

export default function Navbar() {
  return (
    <nav className="navbar">
      <div className="navbar-container">
        <Link to="/" className="navbar-logo">
          <div className="logo-text">
            <span className="logo-alpha">Quant Agent</span>
            <span className="logo-arena">Trader</span>
          </div>
        </Link>

        <ul className="navbar-menu-center">
          <li><NavLink to="/live" className={({ isActive }) => (isActive ? 'active' : '')}>实况</NavLink></li>
          <li className="separator">|</li>
          <li><NavLink to="/leaderboard" className={({ isActive }) => (isActive ? 'active' : '')}>模型排行榜</NavLink></li>
          <li className="separator">|</li>
          <li><NavLink to="/control" className={({ isActive }) => (isActive ? 'active' : '')}>总控</NavLink></li>
          <li className="separator">|</li>
          <li><NavLink to="/data-platform" className={({ isActive }) => (isActive ? 'active' : '')}>数据</NavLink></li>
          <li className="separator">|</li>
          <li><NavLink to="/harness" className={({ isActive }) => (isActive ? 'active' : '')}>交易智能体</NavLink></li>
          <li className="separator">|</li>
          <li><NavLink to="/about" className={({ isActive }) => (isActive ? 'active' : '')}>关于</NavLink></li>
        </ul>

        <div className="navbar-right">
          <Link to="/live" className="navbar-link">
            3 市场 × 2 模型 · 第 1 赛季
          </Link>
        </div>
      </div>
    </nav>
  );
}
