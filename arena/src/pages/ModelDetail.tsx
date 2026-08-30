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
import ModelChat from '../components/ModelChat';
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
          ← 返回
        </Link>
        <span style={{ fontSize: 26, lineHeight: 1 }}>{logoOf(name)}</span>
        <h1 style={{ fontSize: 20, textTransform: 'uppercase', letterSpacing: '0.5px', fontFamily: "'Courier New', monospace" }}>
          {shortName(name)}
        </h1>
        <span className="chip" style={{ borderRadius: 0 }}>{meta.label}</span>
        <span style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 10, letterSpacing: '0.12em', fontFamily: "'Courier New', monospace" }}>
          {meta.name} · 30 秒自动刷新
        </span>
      </div>

      <div className="stat-grid" style={{ marginBottom: 20 }}>
        <StatCard k="当前权益" v={fmtMoney(s?.end_equity, meta.currency)} sub={`起始 ${fmtMoney(s?.start_equity, meta.currency)}`} />
        <StatCard k="累计盈亏" v={fmtMoney((s?.end_equity ?? 0) - (s?.start_equity ?? 0), meta.currency, 1)} className={pnlClass((s?.end_equity ?? 0) - (s?.start_equity ?? 0))} sub={`= 收益 ${fmtPct(s?.total_return)}`} />
        <StatCard k="总收益率" v={fmtPct(s?.total_return)} className={pnlClass(s?.total_return)} />
        <StatCard k="最大回撤" v={fmtPct(s?.max_drawdown, 2, false)} className="down" />
        <StatCard k="夏普比率" v={fmtNum(s?.sharpe)} sub="日频 × √252" />
        <StatCard k="胜率" v={s?.win_rate != null ? fmtPct(s.win_rate, 1, false) : '—'} sub={`已平仓 ${s?.closed_trades ?? 0} 笔`} />
        <StatCard k="盈亏比" v={s?.profit_factor != null ? fmtNum(s.profit_factor) : '—'} sub="盈利合计 / 亏损合计" />
        <StatCard k="累计费用" v={s?.total_fee != null ? fmtMoney(s.total_fee, meta.currency, 1) : '—'} sub={s?.fee_ratio != null ? `占本金 ${fmtPct(s.fee_ratio, 3, false)}` : ''} />
        <StatCard k="平均持仓" v={s?.avg_hold_days != null ? `${fmtNum(s.avg_hold_days, 1)} 天` : '—'} sub={`持仓占比 ${s?.position_time_ratio != null ? fmtPct(s.position_time_ratio, 1, false) : '—'}`} />
        <StatCard k="成交笔数" v={trades.data?.length ?? 0} sub={`${(trades.data ?? []).filter((t) => t.action === 'buy').length} 买 / ${(trades.data ?? []).filter((t) => t.action === 'sell').length} 卖`} />
        <StatCard k="平均单笔费用" v={trades.data?.length ? fmtMoney((s?.total_fee ?? 0) / Math.max(trades.data.length, 1), meta.currency, 2) : '—'} sub="累计费用 / 成交笔数" />
        <StatCard k="净值记录" v={s?.records ?? 0} sub={`最新 ${perf.data?.points[perf.data.points.length - 1]?.date?.slice(0, 10) ?? '—'}`} />
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-title">账户净值 <span className="faint">虚线 = 基准指数</span></div>
        <EquityChart lines={chartLine ? [chartLine] : []} benchmark={benchLine} currency={meta.currency} height={340} />
      </div>

      <div className="panel">
        <div className="tabs">
          <button className={`tab ${tab === 'positions' ? 'active' : ''}`} onClick={() => setTab('positions')}>
            持仓
          </button>
          <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>
            成交 ({trades.data?.length ?? 0})
          </button>
          <button className={`tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
            决策日志
          </button>
        </div>
        {tab === 'positions' && <PositionsTable records={positions.data ?? []} currency={meta.currency} />}
        {tab === 'trades' && <TradesTable records={trades.data ?? []} currency={meta.currency} />}
        {tab === 'logs' && (
          <ModelChat
            logs={logs.data ?? []}
            trades={trades.data ?? []}
            positions={positions.data ?? []}
            model={name}
            currency={meta.currency}
          />
        )}
      </div>
    </div>
  );
}
