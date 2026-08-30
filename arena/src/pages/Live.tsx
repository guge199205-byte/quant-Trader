import { useCallback, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  BenchPoint,
  LogLine,
  MarketId,
  OverviewRow,
  PositionRecord,
  TradeRecord,
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
import ModelCard, { logoOf } from '../components/ModelCard';
import { MarketSwitcher } from '../components/Navbar';
import { fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Live.css';

/** 模型色（coke AccountValueChart 风格） */
const MODEL_COLORS: Record<string, string> = {
  'deepseek-v4-flash': '#4d6bfe',
  'deepseek-v4-pro': '#8b5cf6',
};
const FALLBACK_COLOR = '#5a5a5a';
const BENCH_COLOR = '#10a37f';

type Tab = 'all' | '5d' | 'completed' | 'chat' | 'positions' | 'readme';
type TimeRange = 'all' | '5d';

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: '5d', label: '近5日' },
  { id: 'completed', label: '已平仓' },
  { id: 'chat', label: '模型对话' },
  { id: 'positions', label: '持仓' },
  { id: 'readme', label: '说明' },
];

interface TradeEvt {
  date: string;
  side: 'buy' | 'sell';
  symbol: string;
  amount: number;
  cash: number;
}

const benchLabelOf = (market: MarketId): string =>
  market === 'us' ? 'NDX100' : market === 'cn' ? 'SSE50' : 'HSI';

/** Live 终端页 —— 复刻 coke-nof1：
 *  顶部价格条 + HIGHEST/LOWEST → 左净值图 + 模型横排卡 → 右 360px 六 tab 面板 */
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

  const [tab, setTab] = useState<Tab>('all');
  const [chartRange, setChartRange] = useState<TimeRange>('all');
  const [chartMode, setChartMode] = useState<'pct' | 'dollar'>('pct');
  const [selectedModel, setSelectedModel] = useState<string>('all');

  // 总控聚合（三市场一次拉取）
  const overview = usePolling(() => fetchOverview(), [], 30000);
  const rows: OverviewRow[] = useMemo(
    () => overview.data?.markets[market] ?? [],
    [overview.data, market],
  );
  const agentsKey = rows.map((r) => r.name).join('|');

  // 基准指数（US=等权 NDX100 / CN=SSE50 / HK 暂无）
  const bench = usePolling(() => fetchBenchmark(market), [market], 300000);

  // 当前市场全部 agent 净值序列
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
        toChartLine(p.agent, p.agent, MODEL_COLORS[p.agent] ?? FALLBACK_COLOR, p.points),
      ),
    [perfs.data],
  );

  const benchLine = useMemo(
    () =>
      bench.data && bench.data.length
        ? toBenchLine(benchLabelOf(market), BENCH_COLOR, bench.data)
        : null,
    [bench.data, market],
  );

  // 右侧面板数据源（FILTER 选中模型；'all' → 第一个 agent）
  const effectiveModel = selectedModel === 'all' ? (rows[0]?.name ?? null) : selectedModel;

  const positions = usePolling<PositionRecord[]>(
    () => (effectiveModel ? fetchPositions(effectiveModel, market) : Promise.resolve([])),
    [effectiveModel, market],
    30000,
  );
  const trades = usePolling<TradeRecord[]>(
    () => (effectiveModel ? fetchTrades(effectiveModel, market) : Promise.resolve([])),
    [effectiveModel, market],
    30000,
  );
  const logs = usePolling<LogLine[]>(
    () => (effectiveModel ? fetchLogs(effectiveModel, market) : Promise.resolve([])),
    [effectiveModel, market],
    30000,
  );

  // ---------- 事件流（成交，/trades 顶层字段） ----------
  const tradeEvents: TradeEvt[] = useMemo(
    () =>
      (trades.data ?? [])
        .map((r) => ({
          date: r.date,
          side: (r.action ?? '').toLowerCase() === 'buy' ? 'buy' as const : 'sell' as const,
          symbol: r.symbol,
          amount: r.amount,
          cash: r.cash_after ?? 0,
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
    [trades.data],
  );

  const recentDays = useMemo(() => {
    const dates = [...new Set(tradeEvents.map((e) => e.date))].sort();
    return new Set(dates.slice(-5));
  }, [tradeEvents]);

  // ---------- 顶部价格条（基准 + 最高/最低表演者） ----------
  const benchStats = useMemo(() => {
    const pts: BenchPoint[] = bench.data ?? [];
    if (pts.length < 2) return { last: null, dayChange: null };
    const last = pts[pts.length - 1].close;
    const prev = pts[pts.length - 2].close;
    return { last, dayChange: prev ? (last - prev) / prev : null };
  }, [bench.data]);

  const performers = useMemo(() => {
    const list = rows
      .map((r) => ({ name: r.name, ret: r.summary?.total_return ?? null }))
      .filter((p) => p.ret != null)
      .sort((a, b) => (b.ret as number) - (a.ret as number));
    return { highest: list[0] ?? null, lowest: list[list.length - 1] ?? null };
  }, [rows]);

  // ---------- 右侧列表渲染 ----------
  const renderList = () => {
    if (tab === 'readme') {
      return (
        <div className="readme-body">
          <h4>BayMax Arena</h4>
          <p>
            多 AI 模型以独立资金池在 <b>美股 / A股 / 港股</b> 三市场自主分析、决策、买卖，
            全自主零样本交易，无微调、无人工干预。
          </p>
          <h4>模型对决</h4>
          <p>
            <b>DeepSeek V4 Flash</b> · <b>DeepSeek V4 Pro</b> —— 同一数据、同一工具集、
            同一起点资金，公平竞技。
          </p>
          <h4>市场</h4>
          <p>US 等权 NDX100 · CN SSE50 · HK 恒指成分 · 数据更新至 {rows[0]?.latest_date ?? '—'}</p>
          <h4>成本模型</h4>
          <p>双边费率 0.03% × 2 + 滑点 ±0.05%，成交价取自本地数据仓库日线。</p>
          <h4>风控</h4>
          <p>单笔/持仓限额、日亏熔断、现金保留、黑名单，三条交易路径单点拦截。</p>
          <h4>Fair Play</h4>
          <p>同起点资金、同一数据切片、同一工具集；历史回放防未来函数。</p>
          <h4>为什么是智能体</h4>
          <p>
            不是写死的买卖规则——每个 agent 是 LLM 智能体：读行情 → 推理分析 →
            调工具下单，每笔交易都有<b>决策日志</b>可回看"为什么买/卖"。
            同一套能力直接适配三市场，无需逐市场重写策略。
          </p>
          <h4>记忆与进化</h4>
          <p>每市场独立交易记忆：开盘读心得、收盘写经验，agent 自己沉淀策略，越用越好用。</p>
          <div className="readme-links">
            <Link to="/leaderboard">排行榜</Link>
            <span>·</span>
            <Link to="/models">模型</Link>
            <span>·</span>
            <Link to="/control">总控</Link>
          </div>
        </div>
      );
    }

    if (tab === 'positions') {
      const last = positions.data?.[positions.data.length - 1];
      if (!last) return <div className="empty-state">暂无持仓数据</div>;
      const entries = Object.entries(last.positions ?? {}).filter(([sym]) => sym !== 'CASH');
      const cash = Number(last.positions?.CASH ?? 0);
      return (
        <div style={{ padding: '8px 12px' }}>
          <div className="pos-row">
            <span className="pos-sym">现金 CASH</span>
            <span className="pos-cash">{fmtMoney(cash, meta.currency)}</span>
          </div>
          {entries.length === 0 && (
            <div className="empty-state" style={{ padding: '24px 0' }}>空仓 — 无持仓</div>
          )}
          {entries.map(([sym, qty]) => (
            <div className="pos-row" key={sym}>
              <span className="pos-sym">{sym}</span>
              <span className="pos-qty">{Number(qty).toLocaleString('en-US')}</span>
            </div>
          ))}
        </div>
      );
    }

    if (tab === 'chat') {
      const msgs = (logs.data ?? []).flatMap((l) =>
        (l.new_messages ?? []).map((m) => m.content).filter((c): c is string => !!c),
      );
      if (!msgs.length) return <div className="empty-state">暂无决策日志</div>;
      return (
        <>
          {msgs.map((content, i) => (
            <div className="trade-list-item" key={i}>
              <div className="trade-item-header">
                <span className="trade-side info">AI 决策</span>
                <span className="trade-item-time">{logoOf(effectiveModel ?? '')}</span>
              </div>
              <div className="msg-content">{content}</div>
            </div>
          ))}
        </>
      );
    }

    // ALL / 5D / COMPLETED
    let evts = tradeEvents;
    if (tab === '5d') evts = tradeEvents.filter((e) => recentDays.has(e.date));
    if (tab === 'completed') evts = tradeEvents.filter((e) => e.side === 'sell');
    if (!evts.length) return <div className="empty-state">暂无成交</div>;
    return (
      <>
        {evts.map((e, i) => (
          <div className="trade-list-item" key={`${e.date}-${i}`}>
            <div className="trade-item-header">
              <span className="trade-item-time">{e.date.slice(0, 10)}</span>
              <span className={`trade-side ${e.side}`}>{e.side === 'buy' ? '买入' : '卖出'}</span>
            </div>
            <div className="trade-details">
              <span className="trade-symbol">{e.symbol}</span>
              <span className="trade-qty">× {e.amount}</span>
              <span className="trade-pnl">{fmtMoney(e.cash, meta.currency)}</span>
            </div>
            <div className="trade-cash">现金 {fmtMoney(e.cash, meta.currency)}</div>
          </div>
        ))}
      </>
    );
  };

  if (overview.error) {
    return (
      <div className="error-box">
        API 连接失败：{overview.error}
        <br /><br />
        请确认 baymax-api(8091) 与 ui-arena(8092) 容器已启动
      </div>
    );
  }

  return (
    <div className="live">
      {/* 顶部状态条：价格 + 表演者 + 市场切换 */}
      <div className="top-status-bar">
        <div className="status-group">
          <div className="price-item">
            <span className="price-label">{benchLabelOf(market)} 指数</span>
            <span className="price-value">{benchStats.last != null ? fmtMoney(benchStats.last) : '—'}</span>
            <span className={`price-change ${benchStats.dayChange != null ? pnlClass(benchStats.dayChange) : 'dim'}`}>
              {benchStats.dayChange != null ? fmtPct(benchStats.dayChange) : '无行情'}
            </span>
          </div>
          <div className="performers">
            <div className="performer">
              <span className="performer-label">最高</span>
              <span className="performer-value">
                {performers.highest ? (
                  <>{performers.highest.name} <b className="up">{fmtPct(performers.highest.ret)}</b></>
                ) : '—'}
              </span>
            </div>
            <div className="performer">
              <span className="performer-label">最低</span>
              <span className="performer-value">
                {performers.lowest ? (
                  <>{performers.lowest.name} <b className="down">{fmtPct(performers.lowest.ret)}</b></>
                ) : '—'}
              </span>
            </div>
          </div>
        </div>
        <MarketSwitcher market={market} onChange={switchMarket} />
      </div>

      <div className="main-content">
        {/* 左：图表 + 模型卡 */}
        <div className="chart-area">
          <div className="chart-header">
            <div className="chart-title">总账户净值</div>
            <div className="chart-controls">
              <button className={`time-btn ${chartRange === 'all' ? 'active' : ''}`} onClick={() => setChartRange('all')}>
                全部
              </button>
              <button className={`time-btn ${chartRange === '5d' ? 'active' : ''}`} onClick={() => setChartRange('5d')}>
                近5日
              </button>
              <span style={{ width: 1, height: 16, background: '#000', margin: '0 2px' }} />
              <button className={`time-btn ${chartMode === 'dollar' ? 'active' : ''}`} onClick={() => setChartMode('dollar')}>
                $
              </button>
              <button className={`time-btn ${chartMode === 'pct' ? 'active' : ''}`} onClick={() => setChartMode('pct')}>
                %
              </button>
            </div>
          </div>
          {overview.loading && !rows.length ? (
            <div className="loading"><div className="spinner" />加载中…</div>
          ) : (
            <>
              <EquityChart
                lines={lines}
                benchmark={benchLine}
                currency={meta.currency}
                mode={chartMode}
                timeRange={chartRange}
                height="clamp(360px, 44vw, 560px)"
              />
              <div className="chart-legend" style={{ marginTop: 6 }}>
                {lines.map((l) => (
                  <span className="legend-item" key={l.id}>
                    <span style={{ color: l.color }}>▬</span> {l.label}
                  </span>
                ))}
                {benchLine && (
                  <span className="legend-item">
                    <span style={{ color: benchLine.color }}>- -</span> {benchLine.label}
                  </span>
                )}
              </div>

              <div className="model-cards-section">
                {(perfs.data ?? []).map((p) => (
                  <ModelCard
                    key={p.agent}
                    market={market}
                    agent={p.agent}
                    balance={p.summary?.end_equity ?? null}
                    ret={p.summary?.total_return ?? null}
                    selected={p.agent === effectiveModel}
                    onClick={() =>
                      setSelectedModel((cur) => (cur === p.agent ? 'all' : p.agent))
                    }
                  />
                ))}
                {!perfs.data?.length && <div className="empty-state">该市场暂无 Agent</div>}
              </div>
            </>
          )}
        </div>

        {/* 右：360px 面板 */}
        <div className="right-section">
          <div className="trade-tabs">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`trade-tab ${tab === t.id ? 'active' : ''}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="filter-bar">
            <span className="filter-label">模型</span>
            <select
              className="filter-select"
              value={effectiveModel ?? ''}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {rows.map((r) => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
            </select>
            <span className="filter-count">
              {tab === 'chat'
                ? (logs.data ?? []).length
                : tab === 'positions'
                  ? Object.keys(positions.data?.[positions.data.length - 1]?.positions ?? {}).length
                  : (tab === '5d'
                      ? tradeEvents.filter((e) => recentDays.has(e.date)).length
                      : tab === 'completed'
                        ? tradeEvents.filter((e) => e.side === 'sell').length
                        : tradeEvents.length)}
            </span>
          </div>
          <div className="trade-list">{renderList()}</div>
        </div>
      </div>
    </div>
  );
}
