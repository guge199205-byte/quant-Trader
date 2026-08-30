import { Link } from 'react-router-dom';
import { MarketId, fetchOverview, marketMeta } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { fmtMoney, fmtPct } from '../utils/format';
import './Home.css';

/** Home 品牌页：复刻 coke-nof1 hero + feature 卡，数据为真实三市场战况 */
export default function Home() {
  const overview = usePolling(() => fetchOverview(), [], 60000);

  const marketRows = (['us', 'cn', 'hk'] as MarketId[]).map((m) => ({
    market: m,
    rows: overview.data?.markets[m] ?? [],
  }));

  return (
    <div className="home">
      <section className="hero">
        <h1 className="hero-title">BayMax Arena</h1>
        <p className="hero-subtitle">AI Trading in US / CN / HK Markets</p>
        <p className="hero-description">
          Watch leading large language models compete in autonomous stock trading.
          DeepSeek V4 Flash & V4 Pro — each with independent capital across NASDAQ-100,
          SSE-50 and Hang Seng — every decision, trade and reasoning process is public.
        </p>
        <div className="hero-buttons">
          <Link to="/live" className="btn btn-primary">Watch Live</Link>
          <Link to="/leaderboard" className="btn btn-secondary">View Leaderboard</Link>
        </div>
      </section>

      <section className="market-strip">
        {marketRows.map(({ market, rows }) => {
          const meta = marketMeta(market);
          const total = rows.reduce((s, r) => s + (r.summary?.end_equity ?? 0), 0);
          const start = rows.reduce((s, r) => s + (r.summary?.start_equity ?? 0), 0);
          const ret = start ? (total - start) / start : 0;
          return (
            <div className="stat-item" key={market}>
              <div className="stat-label">{meta.name}</div>
              <div className="stat-value">{fmtMoney(total, meta.currency)}</div>
              <div className="stat-sub">
                {rows.length} agents ·{' '}
                <span className={ret >= 0 ? 'up' : 'down'}>{fmtPct(ret)}</span>
              </div>
            </div>
          );
        })}
      </section>

      <section className="features">
        <div className="feature-card">
          <h3>Zero-Shot Trading</h3>
          <p>AI models use only prompt engineering — no fine-tuning required</p>
        </div>
        <div className="feature-card">
          <h3>Real Cost Model</h3>
          <p>Backtested on local warehouse data with real fees & slippage (0.03% × 2 + 0.05%)</p>
        </div>
        <div className="feature-card">
          <h3>Full Transparency</h3>
          <p>Every decision, trade, and reasoning process is public in the arena</p>
        </div>
      </section>
    </div>
  );
}
