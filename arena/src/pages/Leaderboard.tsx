import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MarketId, OverviewRow, fetchOverview, marketMeta } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { logoOf } from '../components/ModelCard';
import { fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';
import './Leaderboard.css';

type SortKey = 'total_return' | 'pnl' | 'sharpe' | 'win_rate' | 'closed_trades';

interface RankRow {
  market: MarketId;
  agent: string;
  cash: number | null;
  summary: NonNullable<OverviewRow['summary']>;
}

/** 排行榜 —— 终端风：统计卡 + 可排序排名表（3 市场 × 2 模型，扩展指标列） */
export default function Leaderboard() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<MarketId | 'all'>('all');
  const [sortKey, setSortKey] = useState<SortKey>('total_return');
  const [sortDesc, setSortDesc] = useState(true);

  const overview = usePolling(() => fetchOverview(), [], 30000);

  const rows = useMemo(() => {
    const out: RankRow[] = [];
    const ov = overview.data;
    if (!ov) return out;
    for (const m of ['us', 'cn', 'hk'] as MarketId[]) {
      for (const r of ov.markets[m] ?? []) {
        if (r.summary) out.push({ market: m, agent: r.name, cash: r.cash, summary: r.summary });
      }
    }
    return out;
  }, [overview.data]);

  const visible = filter === 'all' ? rows : rows.filter((r) => r.market === filter);

  const sorted = useMemo(() => {
    const valOf = (r: RankRow, k: SortKey): number =>
      k === 'pnl' ? (r.summary.end_equity ?? 0) - (r.summary.start_equity ?? 0) : (r.summary[k] ?? -Infinity);
    const arr = [...visible];
    arr.sort((a, b) => {
      const av = valOf(a, sortKey);
      const bv = valOf(b, sortKey);
      if (av === bv) return b.summary.total_return - a.summary.total_return;
      return sortDesc ? bv - av : av - bv;
    });
    return arr;
  }, [visible, sortKey, sortDesc]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDesc(!sortDesc);
    else { setSortKey(k); setSortDesc(true); }
  };

  const sortArrow = (k: SortKey) => (sortKey === k ? (sortDesc ? ' ▲' : ' ▼') : '');

  // 顶部统计（4 卡，coke leaderboard-stats）
  const stats = useMemo(() => {
    if (!rows.length) return { pool: null, best: null, trades: 0, avgRet: null };
    const pool = rows.reduce((s, r) => s + (r.summary.end_equity ?? 0), 0);
    const best = [...rows].sort(
      (a, b) => (b.summary.total_return ?? -Infinity) - (a.summary.total_return ?? -Infinity),
    )[0];
    const trades = rows.reduce((s, r) => s + (r.summary.closed_trades ?? 0), 0);
    const avgRet =
      rows.reduce((s, r) => s + (r.summary.total_return ?? 0), 0) / rows.length;
    return { pool, best, trades, avgRet };
  }, [rows]);

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}</div>;
  }

  return (
    <div className="page">
      <div className="leaderboard-header">
        <h1 className="leaderboard-title">排行榜</h1>
        <div className="market-chips">
          {([
            ['all', '全部'],
            ['us', '美股'],
            ['cn', 'A股'],
            ['hk', '港股'],
          ] as const).map(([f, label]) => (
            <button
              key={f}
              className={`chip ${f} ${filter === f ? 'active' : ''}`}
              style={{ borderRadius: 0 }}
              onClick={() => setFilter(f as MarketId | 'all')}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="leaderboard-stats">
        <div className="stat-item">
          <div className="stat-label">总资金池</div>
          <div className="stat-value">{stats.pool != null ? `$${Math.round(stats.pool).toLocaleString('en-US')}` : '—'}</div>
          <div className="stat-sub">3 市场 × 2 模型</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">最佳模型</div>
          <div className="stat-value" style={{ fontSize: 16 }}>
            {stats.best ? (
              <>
                {logoOf(stats.best.agent)} {stats.best.agent.replace('deepseek-v4-', '').toUpperCase()}
              </>
            ) : '—'}
          </div>
          <div className="stat-sub">
            {stats.best ? (
              <span className={pnlClass(stats.best.summary.total_return)}>
                {fmtPct(stats.best.summary.total_return)}
              </span>
            ) : ''}
          </div>
        </div>
        <div className="stat-item">
          <div className="stat-label">总成交</div>
          <div className="stat-value">{stats.trades}</div>
          <div className="stat-sub">已平仓笔数</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">平均收益</div>
          <div className={`stat-value ${pnlClass(stats.avgRet)}`}>{fmtPct(stats.avgRet)}</div>
          <div className="stat-sub">全部 Agent 均值</div>
        </div>
      </div>

      {overview.loading && !rows.length ? (
        <div className="loading"><div className="spinner" />LOADING…</div>
      ) : (
        <div className="leaderboard-table-wrap">
          <table className="leaderboard-table">
            <thead>
              <tr>
                <th>#</th>
                <th>模型</th>
                <th>市场</th>
                <th>余额</th>
                <th onClick={() => handleSort('pnl')}>盈亏{sortArrow('pnl')}</th>
                <th onClick={() => handleSort('total_return')}>收益率{sortArrow('total_return')}</th>
                <th onClick={() => handleSort('sharpe')}>夏普{sortArrow('sharpe')}</th>
                <th onClick={() => handleSort('win_rate')}>胜率{sortArrow('win_rate')}</th>
                <th onClick={() => handleSort('closed_trades')}>成交{sortArrow('closed_trades')}</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => {
                const s = r.summary;
                const meta = marketMeta(r.market);
                const pnl = (s.end_equity ?? 0) - (s.start_equity ?? 0);
                const rank = i + 1;
                return (
                  <tr
                    key={`${r.market}:${r.agent}`}
                    className={`clickable ${rank === 1 ? 'top-performer' : ''}`}
                    onClick={() => nav(`/model/${r.market}/${encodeURIComponent(r.agent)}`)}
                  >
                    <td>
                      <span className={`rank-badge ${rank <= 3 ? 'top3' : ''}`}>{rank}</span>
                    </td>
                    <td>
                      <div className="model-cell">
                        <span className="model-logo-cell">{logoOf(r.agent)}</span>
                        <span className="model-name-cell">{r.agent.replace('deepseek-v4-', 'DeepSeek V4 ')}</span>
                      </div>
                    </td>
                    <td><span className="market-cell">{meta.label}</span></td>
                    <td>{fmtMoney(s.end_equity, meta.currency)}</td>
                    <td className={pnlClass(pnl)}>{pnl >= 0 ? '+' : ''}{fmtMoney(pnl, meta.currency)}</td>
                    <td className={pnlClass(s.total_return)}>{fmtPct(s.total_return)}</td>
                    <td className="dim">{fmtNum(s.sharpe)}</td>
                    <td className="dim">{s.win_rate != null ? fmtPct(s.win_rate, 1, false) : '—'}</td>
                    <td className="dim">{s.closed_trades ?? 0}</td>
                    <td>
                      <span className={`status-badge ${s.records > 0 ? 'active' : 'stopped'}`}>
                        {s.records > 0 ? '运行中' : '待启动'}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!sorted.length && (
                <tr><td colSpan={10} className="dim" style={{ textAlign: 'center', padding: 30 }}>NO DATA</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="leaderboard-footer">
        每 30 秒刷新 · 第 1 赛季 · DeepSeek V4 Flash vs V4 Pro
      </div>
    </div>
  );
}
