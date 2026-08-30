import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  LogLine,
  MarketId,
  OverviewRow,
  PositionRecord,
  fetchBenchmark,
  fetchLogs,
  fetchOverview,
  fetchPerformance,
  fetchPositions,
  fetchTrades,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine } from '../components/EquityChart';
import ModelCard from '../components/ModelCard';
import StatusStrip from '../components/StatusStrip';
import { PositionsTable, TradesTable } from '../components/Tables';
import DecisionLog from '../components/DecisionLog';
import { MarketSwitcher } from '../components/Navbar';

const MARKET_COLOR: Record<MarketId, string> = { us: 'var(--us)', cn: 'var(--cn)', hk: 'var(--hk)' };

/** Live 实盘观战：市场切换 → 状态条 + 模型卡网格 + 净值对比图 + 持仓/成交/决策日志 */
export default function Live() {
  const [params, setParams] = useSearchParams();
  const rawMarket = params.get('market');
  const market: MarketId = (['us', 'cn', 'hk'] as MarketId[]).includes(rawMarket as MarketId)
    ? (rawMarket as MarketId)
    : 'us';
  const switchMarket = useCallback(
    (m: MarketId) => setParams({ market: m }, { replace: true }),
    [setParams],
  );
  const meta = marketMeta(market);

  // 总控聚合（三市场一次拉取）
  const overview = usePolling(() => fetchOverview(), [], 30000);
  const rows: OverviewRow[] = useMemo(
    () => overview.data?.markets[market] ?? [],
    [overview.data, market],
  );
  const agentsKey = rows.map((r) => r.name).join('|');

  // 每市场基准（US=QQQ / CN=SSE50 / HK 无）
  const bench = usePolling(() => fetchBenchmark(market), [market], 300000);

  // 当前市场所有 agent 的净值序列（key 含 agent 名单，名单变化才重拉）
  const perfs = usePolling(
    () =>
      Promise.all(
        rows.map((r) => fetchPerformance(r.name, market).catch(() => null)),
      ).then((list) => list.filter(Boolean) as NonNullable<Awaited<ReturnType<typeof fetchPerformance>>>[]),
    [market, agentsKey],
    30000,
  );

  const lines = useMemo(
    () =>
      (perfs.data ?? []).map((p) =>
        toChartLine(p.agent, p.agent, MARKET_COLOR[market], p.points),
      ),
    [perfs.data, market],
  );

  // 详情 tab：默认第一个有数据的 agent
  const [tab, setTab] = useState<'positions' | 'trades' | 'logs'>('positions');
  const [focusAgent, setFocusAgent] = useState<string | null>(null);
  const activeAgent = focusAgent ?? perfs.data?.[0]?.agent ?? null;

  const positions = usePolling<PositionRecord[]>(
    () => (activeAgent ? fetchPositions(activeAgent, market) : Promise.resolve([])),
    [activeAgent, market],
    30000,
  );
  const trades = usePolling<PositionRecord[]>(
    () => (activeAgent ? fetchTrades(activeAgent, market) : Promise.resolve([])),
    [activeAgent, market],
    30000,
  );
  const logs = usePolling<LogLine[]>(
    () => (activeAgent ? fetchLogs(activeAgent, market) : Promise.resolve([])),
    [activeAgent, market],
    30000,
  );

  const benchLine = useMemo(
    () =>
      bench.data && bench.data.length
        ? toBenchLine(market === 'us' ? 'QQQ' : market === 'cn' ? 'SSE50' : '', '#8a94a6', bench.data)
        : null,
    [bench.data, market],
  );

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}<br /><br />请确认 baymax-api(8091) 与 ui-arena(8092) 容器已启动</div>;
  }

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20 }}>LIVE <span className="accent">/</span> 实盘观战</h1>
        <MarketSwitcher market={market} onChange={switchMarket} />
        <span style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 11, letterSpacing: '0.12em' }}>
          自动刷新 30s · {meta.currency} 计价
        </span>
      </div>

      <StatusStrip
        market={market}
        rows={rows}
        bench={bench.data ?? null}
        benchLabel={market === 'us' ? 'QQQ' : market === 'cn' ? 'SSE50' : ''}
      />

      {overview.loading && !rows.length ? (
        <div className="loading"><div className="spinner" />加载中…</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 12, marginBottom: 20 }}>
            {perfs.data?.map((p) => (
                <ModelCard key={p.agent} market={market} agent={p.agent} equitySeries={p.points} summary={p.summary} />
              ))}
            {!perfs.data?.length && (
              <div className="panel" style={{ padding: 40, gridColumn: '1 / -1' }}>
                <div className="loading">该市场暂无 agent 数据</div>
              </div>
            )}
          </div>

          <div className="panel" style={{ marginBottom: 20 }}>
            <div className="panel-title">
              净值走势对比（{meta.currency} · 归一化 100 起点）
              {benchLine && <span className="faint" style={{ letterSpacing: '0.08em' }}>虚线 = 基准指数</span>}
            </div>
            <EquityChart lines={lines} benchmark={benchLine} currency={meta.currency} />
          </div>

          <div className="panel">
            <div className="panel-title">
              明细
              {perfs.data && perfs.data.length > 1 && (
                <select
                  value={activeAgent ?? ''}
                  onChange={(e) => setFocusAgent(e.target.value)}
                  style={{
                    background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--line-strong)',
                    borderRadius: 6, padding: '4px 10px', fontSize: 12, fontFamily: 'inherit',
                  }}
                >
                  {perfs.data.map((p) => (
                    <option key={p.agent} value={p.agent}>{p.agent}</option>
                  ))}
                </select>
              )}
            </div>
            <div className="tabs">
              <button className={`tab ${tab === 'positions' ? 'active' : ''}`} onClick={() => setTab('positions')}>
                POSITIONS 持仓
              </button>
              <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>
                TRADES 成交
              </button>
              <button className={`tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
                DECISIONS 决策日志
              </button>
            </div>
            {tab === 'positions' && <PositionsTable records={positions.data ?? []} currency={meta.currency} />}
            {tab === 'trades' && <TradesTable records={trades.data ?? []} currency={meta.currency} />}
            {tab === 'logs' && <DecisionLog logs={logs.data ?? []} />}
          </div>
        </>
      )}
    </div>
  );
}

// fetchOverview 放在顶部 import 里（见文件头），避免循环引用
