import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MarketId, OverviewRow, fetchOverview, marketMeta } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { logoOf } from '../components/ModelCard';
import ModelCards from '../components/ModelCards';
import { fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';
import './Leaderboard.css';

type SortKey = 'total_return' | 'pnl' | 'sharpe' | 'win_rate' | 'closed_trades';

interface RankRow {
  market: MarketId;
  agent: string;
  cash: number | null;
  records: number;
  summary: NonNullable<OverviewRow['summary']>;
}

/** 模型排行榜 —— 终端风：统计卡 + 可排序排名表 + 模型卡片网格（原"模型"页融合于此） */
export default function Leaderboard() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<MarketId | 'all'>('cn');
  const [sortKey, setSortKey] = useState<SortKey>('total_return');
  const [sortDesc, setSortDesc] = useState(true);

  const overview = usePolling(() => fetchOverview(), [], 30000);

  const rows = useMemo(() => {
    const out: RankRow[] = [];
    const ov = overview.data;
    if (!ov) return out;
    for (const m of ['cn', 'hk', 'us'] as MarketId[]) {
      for (const r of ov.markets[m] ?? []) {
        if (r.summary) out.push({ market: m, agent: r.name, cash: r.cash, records: r.records ?? 0, summary: r.summary });
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

  // 顶部统计（7 卡，coke leaderboard-stats）—— 币种分开：us 是 $，cn/hk 是 ¥，混加无意义
  const stats = useMemo(() => {
    if (!rows.length) return { poolUsd: null, poolCny: null, best: null, trades: 0, avgRet: null, bestSharpe: null, bestWin: null, feeUsd: 0, feeCny: 0, marketCount: 0, agentCount: 0 };
    const usd = (r: RankRow) => (r.market === 'us' ? r.summary.end_equity ?? 0 : 0);
    const cny = (r: RankRow) => (r.market !== 'us' ? r.summary.end_equity ?? 0 : 0);
    const feeUsd = (r: RankRow) => (r.market === 'us' ? r.summary.total_fee ?? 0 : 0);
    const feeCny = (r: RankRow) => (r.market !== 'us' ? r.summary.total_fee ?? 0 : 0);
    const poolUsd = rows.reduce((s, r) => s + usd(r), 0);
    const poolCny = rows.reduce((s, r) => s + cny(r), 0);
    const best = [...rows].sort(
      (a, b) => (b.summary.total_return ?? -Infinity) - (a.summary.total_return ?? -Infinity),
    )[0];
    const trades = rows.reduce((s, r) => s + (r.summary.closed_trades ?? 0), 0);
    const avgRet =
      rows.reduce((s, r) => s + (r.summary.total_return ?? 0), 0) / rows.length;
    const bestSharpe = [...rows].sort(
      (a, b) => (b.summary.sharpe ?? -Infinity) - (a.summary.sharpe ?? -Infinity),
    )[0];
    const bestWin = [...rows].sort(
      (a, b) => (b.summary.win_rate ?? -Infinity) - (a.summary.win_rate ?? -Infinity),
    )[0];
    return {
      poolUsd, poolCny,
      feeUsd: rows.reduce((s, r) => s + feeUsd(r), 0),
      feeCny: rows.reduce((s, r) => s + feeCny(r), 0),
      best, trades, avgRet, bestSharpe, bestWin,
      marketCount: new Set(rows.map((r) => r.market)).size,
      agentCount: new Set(rows.map((r) => r.agent)).size,
    };
  }, [rows]);

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}</div>;
  }

  return (
    <div className="page">
      <div className="leaderboard-header">
        <h1 className="leaderboard-title">模型排行榜</h1>
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
          <div className="stat-label">模拟盘资金池</div>
          <div className="stat-value" style={{ fontSize: 15 }}>
            {stats.poolUsd != null
              ? `$${Math.round(stats.poolUsd).toLocaleString('en-US')} / ¥${Math.round(stats.poolCny).toLocaleString('zh-CN')}`
              : '—'}
          </div>
          <div className="stat-sub">{stats.marketCount} 市场 × {stats.agentCount} 模型 · 第 1 赛季回放</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">最佳模型</div>
          <div className="stat-value" style={{ fontSize: 15 }}>
            {stats.best ? (
              <>
                {logoOf(stats.best.agent)}{' '}
                {/* 显示模型全称（deepseek-v4-flash），缩写 FLASH 看不出是谁 */}
                <span style={{ fontSize: 13 }}>{stats.best.agent}</span>
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
        <div className="stat-item">
          <div className="stat-label">最高夏普</div>
          <div className="stat-value" style={{ fontSize: 16 }}>
            {stats.bestSharpe?.summary.sharpe != null ? fmtNum(stats.bestSharpe.summary.sharpe) : '—'}
          </div>
          <div className="stat-sub">
            {stats.bestSharpe?.summary.sharpe != null ? stats.bestSharpe.agent.replace('deepseek-v4-', '') : ''}
          </div>
        </div>
        <div className="stat-item">
          <div className="stat-label">最高胜率</div>
          <div className="stat-value" style={{ fontSize: 16 }}>
            {stats.bestWin?.summary.win_rate != null ? fmtPct(stats.bestWin.summary.win_rate, 1, false) : '—'}
          </div>
          <div className="stat-sub">
            {stats.bestWin?.summary.win_rate != null ? stats.bestWin.agent.replace('deepseek-v4-', '') : ''}
          </div>
        </div>
        <div className="stat-item">
          <div className="stat-label">累计费用</div>
          <div className="stat-value" style={{ fontSize: 15 }}>
            {stats.poolUsd != null ? `$${Math.round(stats.feeUsd).toLocaleString('en-US')} / ¥${Math.round(stats.feeCny).toLocaleString('zh-CN')}` : '—'}
          </div>
          <div className="stat-sub">双边 0.03% + 滑点 · 分币种</div>
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
                <th>费用</th>
                <th>平均持仓</th>
                <th>最大盈</th>
                <th>最大亏</th>
                <th>平均盈亏</th>
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
                    <td className="dim">{s.total_fee != null ? fmtMoney(s.total_fee, meta.currency, 1) : '—'}</td>
                    <td className="dim">{s.avg_hold_days != null ? `${fmtNum(s.avg_hold_days, 1)}d` : '—'}</td>
                    <td className="up">{s.biggest_win != null ? fmtMoney(s.biggest_win, meta.currency, 1) : '—'}</td>
                    <td className="down">{s.biggest_loss != null ? fmtMoney(s.biggest_loss, meta.currency, 1) : '—'}</td>
                    <td className={pnlClass(s.avg_trade_pnl)}>{s.avg_trade_pnl != null ? fmtMoney(s.avg_trade_pnl, meta.currency, 1) : '—'}</td>
                    <td>
                      {/* 状态用行级 records(有交易记录=跑过); summary 里没有 records 键,
                          之前误用 s.records 恒为 undefined → 全部显示"待启动" */}
                      <span className={`status-badge ${r.records > 0 ? 'active' : 'stopped'}`}>
                        {r.records > 0 ? '运行中' : '待启动'}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!sorted.length && (
                <tr><td colSpan={15} className="dim" style={{ textAlign: 'center', padding: 30 }}>NO DATA</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="leaderboard-footer">
        每 30 秒刷新 · 第 1 赛季(08-24~08-28 回放) ·{' '}
        {[...new Set(rows.map((r) => r.agent.replace('deepseek-v4-', 'V4 ')))].sort().join(' · ')}
      </div>

      <div className="leaderboard-cards-head">
        <span className="leaderboard-cards-title">模型卡片</span>
        <span className="faint" style={{ fontSize: 11, letterSpacing: '0.08em' }}>CLICK TO VIEW DETAIL</span>
      </div>
      <ModelCards filter={filter} />
    </div>
  );
}
