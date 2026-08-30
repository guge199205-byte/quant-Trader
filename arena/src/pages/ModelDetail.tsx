import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  MarketId,
  fetchBenchmark,
  fetchLogs,
  fetchPerformance,
  fetchPositions,
  fetchTrades,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine } from '../components/EquityChart';
import { logoOf, shortName } from '../components/ModelCard';
import StatCard from '../components/StatCard';
import { PositionsTable, TradesTable } from '../components/Tables';
import DecisionLog from '../components/DecisionLog';
import { fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';

const MODEL_COLOR: Record<string, string> = {
  'deepseek-v4-flash': '#4d6bfe',
  'deepseek-v4-pro': '#8b5cf6',
};
const BENCH_COLOR = '#10a37f';

/** 模型详情页：coke 视觉 + 真实扩展指标（Sharpe/胜率/费用/持仓）+ 决策日志 */
export default function ModelDetail() {
  const { market = 'us', agent = '' } = useParams();
  const m = (['us', 'cn', 'hk'] as MarketId[]).includes(market as MarketId) ? (market as MarketId) : 'us';
  const name = decodeURIComponent(agent);
  const meta = marketMeta(m);
  const [tab, setTab] = useState<'positions' | 'trades' | 'logs'>('positions');

  const perf = usePolling(() => fetchPerformance(name, m), [name, m], 30000);
  const positions = usePolling(() => fetchPositions(name, m), [name, m], 30000);
  const trades = usePolling(() => fetchTrades(name, m), [name, m], 30000);
  const logs = usePolling(() => fetchLogs(name, m), [name, m], 30000);
  const bench = usePolling(() => fetchBenchmark(m), [m], 300000);

  const benchLine = useMemo(
    () =>
      bench.data && bench.data.length
        ? toBenchLine(m === 'us' ? 'NDX100' : m === 'cn' ? 'SSE50' : '', BENCH_COLOR, bench.data)
        : null,
    [bench.data, m],
  );

  const chartLine = useMemo(
    () =>
      perf.data
        ? toChartLine(perf.data.agent, perf.data.agent, MODEL_COLOR[name] ?? '#5a5a5a', perf.data.points)
        : null,
    [perf.data, name],
  );

  const s = perf.data?.summary;

  if (perf.error) {
    return (
      <div className="page">
        <div className="error-box">加载失败：{perf.error}</div>
      </div>
    );
  }
  if (perf.loading && !perf.data) {
    return (
      <div className="page">
        <div className="loading"><div className="spinner" />LOADING…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
        <Link to={`/leaderboard`} className="btn" style={{ border: '2px solid #000', borderRadius: 0, fontSize: 12, padding: '8px 16px', background: '#fff', color: '#000', textDecoration: 'none', fontFamily: "'Courier New', monospace" }}>
          ← BACK
        </Link>
        <span style={{ fontSize: 26, lineHeight: 1 }}>{logoOf(name)}</span>
        <h1 style={{ fontSize: 20, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: "'Courier New', monospace" }}>
          {shortName(name)}
        </h1>
        <span className="chip" style={{ borderRadius: 0 }}>{meta.label}</span>
        <span style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 10, letterSpacing: '0.12em', fontFamily: "'Courier New', monospace" }}>
          {meta.name} · 30s AUTO REFRESH
        </span>
      </div>

      <div className="stat-grid" style={{ marginBottom: 20 }}>
        <StatCard k="EQUITY" v={fmtMoney(s?.end_equity, meta.currency)} sub={`START ${fmtMoney(s?.start_equity, meta.currency)}`} />
        <StatCard k="TOTAL RETURN" v={fmtPct(s?.total_return)} className={pnlClass(s?.total_return)} />
        <StatCard k="MAX DRAWDOWN" v={fmtPct(s?.max_drawdown, 2, false)} className="down" />
        <StatCard k="SHARPE" v={fmtNum(s?.sharpe)} sub="DAILY × √252" />
        <StatCard k="WIN RATE" v={s?.win_rate != null ? fmtPct(s.win_rate, 1, false) : '—'} sub={`CLOSED ${s?.closed_trades ?? 0} TRADES`} />
        <StatCard k="PROFIT FACTOR" v={s?.profit_factor != null ? fmtNum(s.profit_factor) : '—'} sub="GROSS WIN / LOSS" />
        <StatCard k="TOTAL FEE" v={s?.total_fee != null ? fmtMoney(s.total_fee, meta.currency, 1) : '—'} sub={s?.fee_ratio != null ? `${fmtPct(s.fee_ratio, 3, false)} OF CAPITAL` : ''} />
        <StatCard k="AVG HOLD" v={s?.avg_hold_days != null ? `${fmtNum(s.avg_hold_days, 1)}D` : '—'} sub={`IN POSITION ${s?.position_time_ratio != null ? fmtPct(s.position_time_ratio, 1, false) : '—'}`} />
        <StatCard k="RECORDS" v={s?.records ?? 0} sub={`LATEST ${perf.data?.points[perf.data.points.length - 1]?.date?.slice(0, 10) ?? '—'}`} />
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-title">ACCOUNT VALUE <span className="faint">DASHED = BENCHMARK</span></div>
        <EquityChart lines={chartLine ? [chartLine] : []} benchmark={benchLine} currency={meta.currency} height={340} />
      </div>

      <div className="panel">
        <div className="tabs">
          <button className={`tab ${tab === 'positions' ? 'active' : ''}`} onClick={() => setTab('positions')}>
            POSITIONS
          </button>
          <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>
            TRADES ({trades.data?.length ?? 0})
          </button>
          <button className={`tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
            DECISIONS
          </button>
        </div>
        {tab === 'positions' && <PositionsTable records={positions.data ?? []} currency={meta.currency} />}
        {tab === 'trades' && <TradesTable records={trades.data ?? []} currency={meta.currency} />}
        {tab === 'logs' && <DecisionLog logs={logs.data ?? []} />}
      </div>
    </div>
  );
}
