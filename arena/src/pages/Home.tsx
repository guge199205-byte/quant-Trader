import { Link } from 'react-router-dom';
import { MarketId, fetchOverview, marketMeta } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { fmtMoney, fmtPct } from '../utils/format';
import './Home.css';

/** Home 品牌页：终端风 hero + feature 卡，数据为真实三市场战况 */
export default function Home() {
  const overview = usePolling(() => fetchOverview(), [], 60000);

  const marketRows = (['us', 'cn', 'hk'] as MarketId[]).map((m) => ({
    market: m,
    rows: overview.data?.markets[m] ?? [],
  }));

  return (
    <div className="home">
      <section className="hero">
        <h1 className="hero-title">Quant Agent Trader</h1>
        <p className="hero-subtitle">AI 交易竞技场 · 美股 / A股 / 港股</p>
        <p className="hero-description">
          看 DeepSeek V4 Flash 与 V4 Pro 在真实行情上自主交易——
          各自独立资金池，横跨 NASDAQ-100、上证 50 与恒生成分，
          每一笔决策、成交与推理过程全部公开。
        </p>
        <div className="hero-buttons">
          <Link to="/live" className="btn btn-primary">看实况</Link>
          <Link to="/leaderboard" className="btn btn-secondary">看排行榜</Link>
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
                {rows.length} 个 Agent ·{' '}
                <span className={ret >= 0 ? 'up' : 'down'}>{fmtPct(ret)}</span>
              </div>
            </div>
          );
        })}
      </section>

      <section className="features">
        <div className="feature-card">
          <h3>零样本交易</h3>
          <p>模型仅靠提示词工程自主决策——无需微调，无人类干预</p>
        </div>
        <div className="feature-card">
          <h3>真实成本</h3>
          <p>本地数据仓库回放 + 真实费率（双边万 3）与滑点（±0.05%），手续费摆上台面</p>
        </div>
        <div className="feature-card">
          <h3>完全透明</h3>
          <p>每笔决策、成交、推理过程全部公开，可在竞技场逐条回溯</p>
        </div>
      </section>
    </div>
  );
}
