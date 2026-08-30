import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MarketId,
  Overview,
  Summary,
  fetchBenchmark,
  fetchOverview,
  fetchPerformance,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine } from '../components/EquityChart';
import { fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';

type SortKey = 'total_return' | 'max_drawdown' | 'sharpe' | 'win_rate' | 'profit_factor' | 'closed_trades' | 'total_fee' | 'fee_ratio' | 'avg_hold_days';

const MARKET_COLOR: Record<MarketId, string> = { us: 'var(--us)', cn: 'var(--cn)', hk: 'var(--hk)' };

interface RankRow {
  market: MarketId;
  agent: string;
  summary: Summary;
}

/** 排行榜：三市场 × 双模型排名表（可排序）+ 归一化净值对比图 */
export default function Leaderboard() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<MarketId | 'all'>('all');
  const [sortKey, setSortKey] = useState<SortKey>('total_return');
  const [sortDesc, setSortDesc] = useState(true);

  const overview = usePolling(() => fetchOverview(), [], 30000);

  // 展开所有 agent 的净值序列（排行榜图）
  const rows = useMemo(() => {
    const out: RankRow[] = [];
    const ov = overview.data as Overview | null;
    if (!ov) return out;
    for (const m of ['us', 'cn', 'hk'] as MarketId[]) {
      for (const r of ov.markets[m] ?? []) {
        if (r.summary) out.push({ market: m, agent: r.name, summary: r.summary });
      }
    }
    return out;
  }, [overview.data]);

  const allKey = rows.map((r) => `${r.market}:${r.agent}`).join('|');
  const perfs = usePolling(
    () =>
      Promise.all(
        rows.map((r) => fetchPerformance(r.agent, r.market).catch(() => null)),
      ).then((list) => list.filter(Boolean) as NonNullable<Awaited<ReturnType<typeof fetchPerformance>>>[]),
    [allKey],
    30000,
  );

  const visible = filter === 'all' ? rows : rows.filter((r) => r.market === filter);

  const sorted = useMemo(() => {
    const arr = [...visible];
    arr.sort((a, b) => {
      const av = a.summary[sortKey] ?? -Infinity;
      const bv = b.summary[sortKey] ?? -Infinity;
      if (av === bv) return b.summary.total_return - a.summary.total_return;
      return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
    return arr;
  }, [visible, sortKey, sortDesc]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDesc(!sortDesc);
    else { setSortKey(k); setSortDesc(true); }
  };

  const sortArrow = (k: SortKey) => (sortKey === k ? (sortDesc ? ' ↓' : ' ↑') : '');

  // 图表：全部 agent（归一化），filter 单市场时叠加该市场基准
  const chartLines = useMemo(() => {
    const agentMarket = new Map(rows.map((r) => [r.agent, r.market]));
    return (perfs.data ?? []).map((p) => {
      const m = agentMarket.get(p.agent) ?? 'us';
      return toChartLine(p.agent, p.agent, MARKET_COLOR[m], p.points);
    });
  }, [perfs.data, rows]);

  const bench = usePolling(
    () => (filter === 'all' ? Promise.resolve(null) : fetchBenchmark(filter)),
    [filter],
    300000,
  );
  const benchLine = useMemo(
    () =>
      bench.data && bench.data.length
        ? toBenchLine(filter === 'us' ? 'QQQ' : 'SSE50', '#8a94a6', bench.data)
        : null,
    [bench.data, filter],
  );

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}</div>;
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20 }}>LEADERBOARD <span className="accent">/</span> 排行榜</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['all', 'us', 'cn', 'hk'] as const).map((f) => (
            <button
              key={f}
              className={`chip ${f} ${filter === f ? 'active' : ''}`}
              onClick={() => setFilter(f as MarketId | 'all')}
            >
              {f === 'all' ? 'ALL' : f.toUpperCase()}
            </button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 11, letterSpacing: '0.12em' }}>
          Season 1 · 2026-08 启动
        </span>
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-title">净值走势对比（归一化 100 起点）</div>
        <EquityChart lines={chartLines} benchmark={benchLine} currency="$" height={400} />
      </div>

      {overview.loading && !rows.length ? (
        <div className="loading"><div className="spinner" />加载中…</div>
      ) : (
        <div className="panel">
          <div className="panel-title">模型排名（点击列头排序 · 点击行看详情）</div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Agent</th>
                  <th>市场</th>
                  <th onClick={() => handleSort('total_return')}>收益率{sortArrow('total_return')}</th>
                  <th onClick={() => handleSort('max_drawdown')}>最大回撤{sortArrow('max_drawdown')}</th>
                  <th onClick={() => handleSort('sharpe')}>Sharpe{sortArrow('sharpe')}</th>
                  <th onClick={() => handleSort('win_rate')}>胜率{sortArrow('win_rate')}</th>
                  <th onClick={() => handleSort('profit_factor')}>盈亏比{sortArrow('profit_factor')}</th>
                  <th onClick={() => handleSort('closed_trades')}>平仓{sortArrow('closed_trades')}</th>
                  <th onClick={() => handleSort('total_fee')}>费用{sortArrow('total_fee')}</th>
                  <th onClick={() => handleSort('fee_ratio')}>费率{sortArrow('fee_ratio')}</th>
                  <th onClick={() => handleSort('avg_hold_days')}>持仓天{sortArrow('avg_hold_days')}</th>
                  <th>权益</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => {
                  const s = r.summary;
                  const meta = marketMeta(r.market);
                  const rank = i + 1;
                  return (
                    <tr key={`${r.market}:${r.agent}`} className="clickable" onClick={() => nav(`/model/${r.market}/${encodeURIComponent(r.agent)}`)}>
                      <td>
                        <span className={`rank-badge ${rank === 1 ? 'rank-1' : rank === 2 ? 'rank-2' : rank === 3 ? 'rank-3' : ''}`}>
                          {rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `#${rank}`}
                        </span>
                      </td>
                      <td style={{ fontWeight: 700 }}>{r.agent}</td>
                      <td><span className="chip" style={{ color: `var(--${r.market})`, borderColor: `var(--${r.market})` }}>{meta.label}</span></td>
                      <td className={pnlClass(s.total_return)}>{fmtPct(s.total_return)}</td>
                      <td className="dim">{fmtPct(s.max_drawdown, 2, false)}</td>
                      <td className="dim">{fmtNum(s.sharpe)}</td>
                      <td className="dim">{s.win_rate != null ? fmtPct(s.win_rate, 1, false) : '—'}</td>
                      <td className="dim">{s.profit_factor != null ? fmtNum(s.profit_factor) : '—'}</td>
                      <td className="dim">{s.closed_trades ?? 0}</td>
                      <td className="dim">{s.total_fee != null ? fmtMoney(s.total_fee, meta.currency, 0) : '—'}</td>
                      <td className="dim">{s.fee_ratio != null ? fmtPct(s.fee_ratio, 3, false) : '—'}</td>
                      <td className="dim">{s.avg_hold_days != null ? fmtNum(s.avg_hold_days, 1) : '—'}</td>
                      <td className="dim">{fmtMoney(s.end_equity, meta.currency)}</td>
                    </tr>
                  );
                })}
                {!sorted.length && (
                  <tr><td colSpan={13} className="faint" style={{ textAlign: 'center', padding: 30 }}>暂无数据</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
