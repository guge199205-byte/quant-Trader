import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
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
  fetchPrices,
  fetchStockNames,
  fetchTrades,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine } from '../components/EquityChart';
import ModelCard from '../components/ModelCard';
import ModelChat from '../components/ModelChat';
import CompletedFeed from '../components/CompletedFeed';
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

type Tab = 'completed' | 'trades' | 'chat' | 'positions' | 'comp' | 'details';
type TimeRange = 'all' | '5d';

/** 右侧 tab：已完成交易 / 成交 / 模型对话 / 持仓 / 比赛配置 / 详情 */
const TABS: { id: Tab; label: string }[] = [
  { id: 'completed', label: '已完成' },
  { id: 'trades', label: '成交' },
  { id: 'chat', label: '模型对话' },
  { id: 'positions', label: '持仓' },
  { id: 'comp', label: '比赛配置' },
  { id: 'details', label: '详情' },
];

interface TradeEvt {
  date: string;
  side: 'buy' | 'sell';
  symbol: string;
  amount: number;
  cash: number;
  price: number | null;
  notional: number | null;
}

const benchLabelOf = (market: MarketId): string =>
  market === 'us' ? 'NDX100' : market === 'cn' ? 'SSE50' : 'HSI';

/** Live 终端页 —— 终端风布局：
 *  顶部价格条 + HIGHEST/LOWEST → 左净值图 + 模型横排卡 → 右 360px 六 tab 面板 */
export default function Live() {
  const [params, setParams] = useSearchParams();
  const rawMarket = params.get('market');
  const market: MarketId = (['cn', 'hk', 'us'] as MarketId[]).includes(rawMarket as MarketId)
    ? (rawMarket as MarketId)
    : 'cn';
  const switchMarket = useCallback(
    (m: MarketId) => setParams({ market: m }, { replace: true }),
    [setParams],
  );
  const meta = marketMeta(market);

  const [tab, setTab] = useState<Tab>('completed');
  const [chartRange, setChartRange] = useState<TimeRange>('all');
  const [chartMode, setChartMode] = useState<'pct' | 'dollar'>('pct');
  const [selectedModel, setSelectedModel] = useState<string>('all');
  const [compMode, setCompMode] = useState(1);

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

  // ---------- 滚动价格条（当前市场全部 agent 持仓股票最新价） ----------
  const prices = usePolling(() => fetchPrices(market), [market], 30000);
  const stockNames = usePolling(() => fetchStockNames(market), [market], 600000);
  const marketPositions = usePolling(
    () =>
      Promise.all(
        rows.map((r) => fetchPositions(r.name, market).catch(() => [] as PositionRecord[])),
      ).then((lists) => lists.flat()),
    [market, agentsKey],
    30000,
  );
  const heldSymbols = useMemo(() => {
    const set = new Set<string>();
    for (const rec of marketPositions.data ?? []) {
      for (const [sym, qty] of Object.entries(rec.positions ?? {})) {
        if (sym !== 'CASH' && Number(qty) > 0) set.add(sym);
      }
    }
    return [...set];
  }, [marketPositions.data]);
  const tickerItems = useMemo(
    () =>
      heldSymbols
        .map((sym) => ({ sym, quote: prices.data?.[sym] ?? null, name: stockNames.data?.[sym] }))
        .filter((t) => t.quote != null),
    [heldSymbols, prices.data, stockNames.data],
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
          price: r.price ?? null,
          notional: r.notional ?? null,
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
    [trades.data],
  );

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
    if (tab === 'comp') {
      const modes = [
        { id: 1, name: 'New Baseline', enabled: true, desc: '数据管道升级：本地数据仓库（后复权日线）、每日数据更新、全自主零样本、支持加仓。三市场（美股 / A股 / 港股）独立竞技。' },
        { id: 2, name: 'Monk Mode', enabled: false, desc: '提示词精简 50%（更短的系统提示词，减少无效推理），同时强化风控护栏：单笔/持仓限额、日亏熔断、现金保留、黑名单。' },
        { id: 3, name: 'Situational Awareness', enabled: false, desc: '模型感知自身排名与对手盈亏：排行榜上下文注入提示词，知己知彼——知道领先多少、落后多少，据此调整进攻/防守节奏。' },
        { id: 4, name: 'Max Leverage', enabled: false, desc: '强制最大杠杆：NDX 标的 20 倍、其他 10 倍，高风险高回报。' },
      ];
      const active = modes.find((m) => m.id === compMode) ?? modes[0];
      return (
        <div className="comp-body">
          <div className="comp-mode-list">
            {modes.map((m) => (
              <button
                key={m.id}
                className={`comp-mode ${compMode === m.id ? 'active' : ''}`}
                onClick={() => setCompMode(m.id)}
              >
                <span className="comp-mode-num">{m.id}</span>
                <span className="comp-mode-name">{m.name}</span>
                <span className={`comp-mode-badge ${m.enabled ? 'on' : ''}`}>
                  {m.enabled ? '当前启用' : '未启用'}
                </span>
              </button>
            ))}
          </div>
          <div className="comp-desc">
            <h4>{active.id} · {active.name}</h4>
            <p>{active.desc}</p>
            {!active.enabled && (
              <p className="comp-note">
                规划中——当前系统实际运行 {modes.find((m) => m.enabled)?.name} 配置。
              </p>
            )}
          </div>
        </div>
      );
    }

    if (tab === 'details') {
      return (
        <div className="readme-body">
          <h4>详情 — 数据管线</h4>
          <p>
            <b>行情</b>：本地数据仓库，后复权日线收盘成交（US/HK 小时格式与 CN 日线格式双兼容）。
          </p>
          <p>
            <b>市场</b>：US 等权 NDX100 · CN SSE50 · HK 恒指成分 · 数据更新至 {rows[0]?.latest_date ?? '—'}
          </p>
          <p><b>基准</b>：NDX100 / SSE50 指数虚线叠加对比。</p>
          <h4>模型</h4>
          <p>
            <b>DeepSeek V4 Flash</b> · <b>DeepSeek V4 Pro</b> —— 同数据、同工具集、同起点资金。
          </p>
          <p>LLM 推理决策，每笔交易有决策日志可回看（MODELCHAT 面板）。</p>
          <h4>实时性</h4>
          <p>30 秒自动刷新；行情 / 持仓 / 成交 / FIFO 平仓明细实时重建。</p>
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
      if (!effectiveModel) return <div className="empty-state">暂无 Agent</div>;
      return (
        <ModelChat
          logs={logs.data ?? []}
          trades={trades.data ?? []}
          positions={positions.data ?? []}
          model={effectiveModel}
          currency={meta.currency}
        />
      );
    }

    // COMPLETED —— 当前市场全部模型平仓消息流（nof1 风格）
    if (tab === 'completed') {
      return (
        <CompletedFeed
          agents={rows.map((r) => r.name)}
          market={market}
          currency={meta.currency}
        />
      );
    }

    // TRADES —— 原始成交详细卡片（选中模型的全部成交）
    if (!tradeEvents.length) return <div className="empty-state">暂无成交</div>;
    return (
      <>
        {tradeEvents.map((e, i) => (
          <div className="trade-card" key={`${e.date}-${i}`}>
            <div className="trade-card-head">
              <span className={`trade-side ${e.side}`}>{e.side === 'buy' ? '买入' : '卖出'}</span>
              <b className="trade-card-symbol">{e.symbol}</b>
              <span className="trade-card-date">{e.date.slice(5)}</span>
            </div>
            <div className="trade-card-grid">
              <span>价格 <b>{e.price != null ? fmtMoney(e.price, meta.currency) : '—'}</b></span>
              <span>数量 <b>{e.amount.toLocaleString('en-US')}</b></span>
              <span>成交金额 <b>{e.notional != null ? fmtMoney(e.notional, meta.currency) : '—'}</b></span>
              <span>现金 <b>{fmtMoney(e.cash, meta.currency)}</b></span>
            </div>
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

      {/* 持仓股票滚动价格条（hover 暂停；速度随持仓数自适应） */}
      {tickerItems.length > 0 && (
        <div
          className="ticker"
          aria-label="持仓股票最新价格"
          style={{ ['--ticker-dur' as string]: `${Math.max(60, tickerItems.length * 4)}s` }}
        >
          <div className="ticker-track">
            {[...tickerItems, ...tickerItems].map((t, i) => {
              const q = t.quote!;
              return (
                <span className="ticker-item" key={`${t.sym}-${i}`}>
                  <span className="ticker-sym">{t.sym}</span>
                  {t.name && <span className="ticker-name">{t.name}</span>}
                  <span className="ticker-price">{fmtMoney(q.price, meta.currency)}</span>
                  <span className={`ticker-chg ${q.change_pct != null ? pnlClass(q.change_pct) : 'dim'}`}>
                    {q.change_pct != null ? fmtPct(q.change_pct) : '—'}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      )}

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
            {tab === 'trades' || tab === 'chat' || tab === 'positions' ? (
              <select
                className="filter-select"
                value={effectiveModel ?? ''}
                onChange={(e) => setSelectedModel(e.target.value)}
              >
                {rows.map((r) => (
                  <option key={r.name} value={r.name}>{r.name}</option>
                ))}
              </select>
            ) : (
              <span className="filter-static">全部模型</span>
            )}
            <span className="filter-count">
              {tab === 'trades'
                ? tradeEvents.length
                : tab === 'chat'
                  ? (logs.data ?? []).length
                  : tab === 'positions'
                    ? Object.keys(positions.data?.[positions.data.length - 1]?.positions ?? {}).length
                    : ''}
            </span>
          </div>
          <div className="trade-list">{renderList()}</div>
        </div>
      </div>
    </div>
  );
}
