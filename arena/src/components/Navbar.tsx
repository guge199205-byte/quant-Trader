import { Link, NavLink, useLocation } from 'react-router-dom';
import { useCallback } from 'react';
import { MarketId } from '../api/client';
import './Navbar.css';

const NAV = [
  { to: '/', label: 'HOME', end: true },
  { to: '/live', label: 'LIVE' },
  { to: '/leaderboard', label: 'LEADERBOARD' },
  { to: '/about', label: 'ABOUT' },
];

export function MarketSwitcher({
  market,
  onChange,
}: {
  market: MarketId;
  onChange: (m: MarketId) => void;
}) {
  return (
    <div className="mkt-switch" role="tablist" aria-label="市场切换">
      {(['us', 'cn', 'hk'] as MarketId[]).map((m) => (
        <button
          key={m}
          role="tab"
          aria-selected={market === m}
          className={`chip ${m} ${market === m ? 'active' : ''}`}
          onClick={() => onChange(m)}
        >
          {m.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export default function Navbar() {
  const loc = useLocation();

  // 保持 /live 与 /model/* 下的市场切换同步（存 URL query）
  const marketFromQuery = useCallback(() => {
    const m = new URLSearchParams(loc.search).get('market');
    return (['us', 'cn', 'hk'] as MarketId[]).includes(m as MarketId) ? (m as MarketId) : null;
  }, [loc.search]);

  return (
    <header className="navbar">
      <div className="navbar-inner">
        <Link to="/" className="brand">
          <span className="brand-mark">▚</span>
          <span className="brand-name">
            BAYMAX<span className="brand-accent">ARENA</span>
          </span>
          <span className="brand-season">S1</span>
        </Link>

        <nav className="nav-links" aria-label="主导航">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>

        <div className="nav-right">
          {marketFromQuery() && <span className="nav-hint">market={marketFromQuery()}</span>}
          <span className="nav-clock" id="arena-clock" />
        </div>
      </div>
    </header>
  );
}
