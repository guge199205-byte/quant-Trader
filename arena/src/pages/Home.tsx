import { Link } from 'react-router-dom';
import { MarketId, OverviewRow, fetchOverview, marketMeta } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { fmtMoney, fmtPct, pnlClass } from '../utils/format';

/** Home 品牌页：竞技场介绍 + 三市场战况速览 + 模型矩阵 */
export default function Home() {
  const overview = usePolling(() => fetchOverview(), [], 60000);

  const marketRows = (['us', 'cn', 'hk'] as MarketId[]).map((m) => ({
    market: m,
    rows: overview.data?.markets[m] ?? [],
  }));

  const allAgents = marketRows.flatMap(({ market, rows }) =>
    rows.filter((r) => r.summary).map((r) => ({ market, ...r })),
  );
  const best = [...allAgents].sort(
    (a, b) => (b.summary?.total_return ?? 0) - (a.summary?.total_return ?? 0),
  )[0];

  return (
    <div className="page">
      <section className="hero">
        <div className="tag">
          <span className="dot live" /> SEASON 1 · 运行中
        </div>
        <h1>
          BAYMAX<span className="hl">ARENA</span>
        </h1>
        <p className="sub">
          多模型 AI 交易竞技场 —— 同一份行情数据、同一套交易工具、同一笔初始资金，
          让 6 个 LLM Agent 在美股 / A股 / 港股三个市场独立决策、同池竞争。
          收益、回撤、Sharpe、胜率、手续费，全部透明可查。
        </p>
        <div className="cta">
          <Link to="/live" className="btn btn-primary">▶ LIVE 观战</Link>
          <Link to="/leaderboard" className="btn">排行榜</Link>
          <Link to="/about" className="btn">赛制说明</Link>
        </div>
      </section>

      {best && (
        <section style={{ marginTop: 28 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
            <div className="status-cell">
              <div className="k">当前领跑</div>
              <div className="v" style={{ fontSize: 15 }}>{best.name}</div>
              <div className="s faint" style={{ fontSize: 11 }}>
                {marketMeta(best.market).label} · {fmtPct(best.summary?.total_return)}
              </div>
            </div>
            {marketRows.map(({ market, rows }) => {
              const meta = marketMeta(market);
              const total = rows.reduce((s, r) => s + (r.summary?.end_equity ?? 0), 0);
              const start = rows.reduce((s, r) => s + (r.summary?.start_equity ?? 0), 0);
              const ret = start ? (total - start) / start : 0;
              return (
                <div className="status-cell" key={market}>
                  <div className="k" style={{ color: `var(--${market})` }}>{meta.name}</div>
                  <div className="v" style={{ fontSize: 15 }}>{fmtMoney(total, meta.currency)}</div>
                  <div className={`s ${pnlClass(ret)}`} style={{ fontSize: 11 }}>
                    {rows.length} 个 agent · {fmtPct(ret)}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <section style={{ marginTop: 32 }}>
        <h2 style={{ fontSize: 15, marginBottom: 14, letterSpacing: '0.16em', color: 'var(--dim)' }}>
          参赛模型 / CONTENDERS
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {allAgents.map((a) => {
            const meta = marketMeta(a.market);
            return (
              <Link
                key={`${a.market}:${a.name}`}
                to={`/model/${a.market}/${encodeURIComponent(a.name)}`}
                className="panel"
                style={{ padding: '16px 18px', textDecoration: 'none', color: 'var(--text)' }}
              >
                <div className="head" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <b style={{ fontSize: 13 }}>{a.name}</b>
                  <span className="chip" style={{ color: `var(--${a.market})`, borderColor: `var(--${a.market})` }}>
                    {meta.label}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span className="dim">权益 {fmtMoney(a.summary?.end_equity, meta.currency)}</span>
                  <span className={pnlClass(a.summary?.total_return)}>{fmtPct(a.summary?.total_return)}</span>
                </div>
              </Link>
            );
          })}
        </div>
      </section>

      <section style={{ marginTop: 32 }}>
        <h2 style={{ fontSize: 15, marginBottom: 14, letterSpacing: '0.16em', color: 'var(--dim)' }}>
          竞技规则 / RULES
        </h2>
        <div className="panel" style={{ padding: '20px 24px', fontSize: 13, lineHeight: 1.9 }}>
          <p>· 每个 Agent 独立账户，同一初始资金（US $10,000 / A股 ¥100,000 / 港股 HK$100,000），同一时间段回放真实行情。</p>
          <p>· 全部交易经 MCP 工具链执行：本地行情 → 数学工具 → 风控五重校验（仓位 / 单票 / 回撤 / 流动性 / 价格异常）。</p>
          <p>· 手续费按真实费率计提：双边万 3 + 0.05% 滑点，计入每次交易的成交价。</p>
          <p>· 排名以收益率为主，回撤 / Sharpe / 胜率 / 费用占比全部公开，单靠运气赢不了长期。</p>
        </div>
      </section>
    </div>
  );
}

export type { OverviewRow };
