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
import StatCard from '../components/StatCard';
import { PositionsTable, TradesTable } from '../components/Tables';
import DecisionLog from '../components/DecisionLog';
import { fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';

const MARKET_COLOR: Record<MarketId, string> = { us: 'var(--us)', cn: 'var(--cn)', hk: 'var(--hk)' };

/** 模型详情：指标卡 + 净值图 + 持仓/成交/决策日志 */
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
        ? toBenchLine(m === 'us' ? 'QQQ' : m === 'cn' ? 'SSE50' : '', '#8a94a6', bench.data)
        : null,
    [bench.data, m],
  );

  const chartLine = useMemo(
    () => (perf.data ? toChartLine(perf.data.agent, perf.data.agent, MARKET_COLOR[m], perf.data.points) : null),
    [perf.data, m],
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
        <div className="loading"><div className="spinner" />加载中…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
        <Link to={`/live?market=${m}`} className="btn">← LIVE</Link>
        <h1 style={{ fontSize: 22 }}>
          {name}
          <span className="chip" style={{ marginLeft: 12, color: `var(--${m})`, borderColor: `var(--${m})` }}>{meta.label}</span>
        </h1>
        <span style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 11, letterSpacing: '0.12em' }}>
          {meta.name} · {meta.currency} 计价 · 30s 自动刷新
        </span>
      </div>

      <div className="stat-grid" style={{ marginBottom: 20 }}>
        <StatCard k="当前权益" v={fmtMoney(s?.end_equity, meta.currency)} sub={`起始 ${fmtMoney(s?.start_equity, meta.currency)}`} />
        <StatCard k="总收益率" v={fmtPct(s?.total_return)} className={pnlClass(s?.total_return)} />
        <StatCard k="最大回撤" v={fmtPct(s?.max_drawdown, 2, false)} className="down" />
        <StatCard k="Sharpe" v={fmtNum(s?.sharpe)} sub="日频 ×√252" />
        <StatCard k="胜率" v={s?.win_rate != null ? fmtPct(s.win_rate, 1, false) : '—'} sub={`平仓 ${s?.closed_trades ?? 0} 笔`} />
        <StatCard k="盈亏比" v={s?.profit_factor != null ? fmtNum(s.profit_factor) : '—'} sub="平均盈利/亏损" />
        <StatCard k="累计费用" v={s?.total_fee != null ? fmtMoney(s.total_fee, meta.currency, 1) : '—'} sub={s?.fee_ratio != null ? `占本金 ${fmtPct(s.fee_ratio, 3, false)}` : ''} />
        <StatCard k="平均持仓" v={s?.avg_hold_days != null ? `${fmtNum(s.avg_hold_days, 1)} 天` : '—'} sub={`持仓占比 ${s?.position_time_ratio != null ? fmtPct(s.position_time_ratio, 1, false) : '—'}`} />
        <StatCard k="净值记录" v={s?.records ?? 0} sub={`最新 ${perf.data?.points[perf.data.points.length - 1]?.date?.slice(0, 10) ?? '—'}`} />
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-title">净值走势（虚线 = 基准指数）</div>
        <EquityChart
          lines={chartLine ? [chartLine] : []}
          benchmark={benchLine}
          currency={meta.currency}
          height={340}
        />
      </div>

      <div className="panel">
        <div className="tabs">
          <button className={`tab ${tab === 'positions' ? 'active' : ''}`} onClick={() => setTab('positions')}>
            POSITIONS 持仓
          </button>
          <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>
            TRADES 成交（{trades.data?.length ?? 0}）
          </button>
          <button className={`tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
            DECISIONS 决策日志
          </button>
        </div>
        {tab === 'positions' && <PositionsTable records={positions.data ?? []} currency={meta.currency} />}
        {tab === 'trades' && <TradesTable records={trades.data ?? []} currency={meta.currency} />}
        {tab === 'logs' && <DecisionLog logs={logs.data ?? []} />}
      </div>
    </div>
  );
}
