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
  fetchLiveAccount,
  fetchLiveEquity,
  fetchLiveLedger,
  fetchLiveTrades,
  fetchLogs,
  fetchOverview,
  fetchTokenUsage,
  fetchPerformance,
  fetchPositions,
  fetchPrices,
  fetchStockNames,
  fetchTrades,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine } from '../components/EquityChart';
import ModelCard, { modelColor } from '../components/ModelCard';
import ModelChat from '../components/ModelChat';
import ChatStream from '../components/ChatStream';
import CompletedFeed from '../components/CompletedFeed';
import CompConfigPanel from '../components/CompConfigPanel';
import { MarketSwitcher } from '../components/Navbar';
import { fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Live.css';

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
  name: string;
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
  const [completedCount, setCompletedCount] = useState(0);

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

  // 实盘账户净值（A股：每分钟采样，前端 20s 轮询尽量实时）
  const liveEquity = usePolling(() => fetchLiveEquity(), [], 20000);
  // 实盘 LLM 分析 token 累计（30s 刷新，模型卡显示）
  const tokenUsage = usePolling(() => fetchTokenUsage(), [], 30000);

  const lines = useMemo(() => {
    const eq = liveEquity.data;
    // 实盘采样必须用完整时间戳 ts（含时刻），date 只是 YYYY-MM-DD 会把当天所有点挤到零点
    const toEq = (v: number, ts: string) => ({ date: ts, cash: 0, market_value: 0, equity: v });
    // A股实盘优先：每 agent 分账虚拟净值线（¥10 万起，通达信桥实时价）
    // + 总账户线（桥实时总资产）。序列 ≥2 点才画。
    if (market === 'cn' && eq) {
      // 分账线只在有实际变动时画（全平 = 尚未分账买入，画了反而干扰）
      const agentLines = Object.entries(eq.agents ?? {})
        .filter(([, pts]) => {
          if (pts.length < 2) return false;
          const vals = pts.map((p) => p.value);
          return Math.min(...vals) !== Math.max(...vals);
        })
        .map(([name, pts]) => ({
          ...toChartLine(
            `live-${name}`,
            name,
            modelColor(name),
            pts.map((e) => toEq(e.value, e.ts)),
          ),
          notional: 100000, // 分账名义基准: hover 换算金额盈亏
        }));
      // 总账户线（¥92.5 万量级）只兜底：没有任何分账线可画时才显示。
      // 用户口径 = 分账 ¥10 万，总账户已买很多、与 10 万不具可比性。
      const totalLine =
        agentLines.length === 0 && (eq.total ?? []).length >= 2
          ? toChartLine(
              'live-total',
              '总账户',
              '#999',
              eq.total.map((e) => toEq(e.value, e.ts)),
            )
          : null;
      if (agentLines.length || totalLine) {
        return [...agentLines, ...(totalLine ? [totalLine] : [])];
      }
    }
    return (perfs.data ?? []).map((p) =>
      toChartLine(p.agent, p.agent, modelColor(p.agent), p.points),
    );
  }, [perfs.data, liveEquity.data, market]);

  // 实盘 5 分钟净值模式（CN 有实盘点）：不画基准线——SSE50 日线会把时间轴拉到 8 月初
  const hasLiveLine = useMemo(() => {
    const eq = liveEquity.data;
    if (market !== 'cn' || !eq) return false;
    if ((eq.total ?? []).length >= 2) return true;
    return Object.values(eq.agents ?? {}).some((pts) => pts.length >= 2);
  }, [market, liveEquity.data]);

  const benchLine = useMemo(
    () =>
      !hasLiveLine && bench.data && bench.data.length
        ? toBenchLine(benchLabelOf(market), BENCH_COLOR, bench.data)
        : null,
    [bench.data, market, hasLiveLine],
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
  // 对话 tab「全部模型」视图：并行拉各模型日志 → 混合时间流
  // 注意用 selectedModel 判断（effectiveModel 会把 all 降级成第一个模型）
  const chatAll = usePolling<{ name: string; lines: LogLine[] }[] | null>(() => {
    if (selectedModel !== 'all' || tab !== 'chat' || !rows.length) return Promise.resolve(null);
    return Promise.all(
      rows.map((r) => fetchLogs(r.name, market).catch(() => [] as LogLine[])),
    ).then((lists) => rows.map((r, i) => ({ name: r.name, lines: lists[i] })));
  }, [selectedModel, tab, rows, market], 30000);

  // ---------- 通达信桥实盘（A股） ----------
  const liveAcct = usePolling(() => fetchLiveAccount(), [], 15000);
  const liveTrades = usePolling(() => fetchLiveTrades(), [], 15000);
  const livePositions = (liveAcct.data?.positions ?? []).filter(
    (p) => Number(p.total_volume) > 0,
  );
  // 实盘分账账本（每 agent ¥10 万虚拟子账户，按模型显示各自持仓）
  const liveLedger = usePolling(() => fetchLiveLedger(), [], 30000);

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

  /** 今日实盘成交（execute 成功，最新在前）；按 ledger 归属标注 agent */
  const ledgerHolderOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [agent, rec] of Object.entries(liveLedger.data?.agents ?? {})) {
      for (const p of rec.positions ?? []) map[p.code] = agent;
    }
    return map;
  }, [liveLedger.data]);
  const liveTradeEvents = (liveTrades.data ?? [])
    .filter(
      (t) =>
        t.mode === 'execute' &&
        t.result &&
        typeof t.result.status === 'string' &&
        t.result.status !== 'rejected' &&
        !String(t.result.message ?? '').includes('签名'),
    )
    .map((t) => ({
      ts: t.ts,
      code: t.code,
      volume: t.volume,
      price: t.price ?? null,
      name: stockNames.data?.[t.code] ?? t.code,
      agent: ledgerHolderOf[t.code] ?? null,
    }))
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
  /** 按选中模型过滤实盘成交（'all' = 全部） */
  const liveTradesFiltered = liveTradeEvents.filter(
    (e) => selectedModel === 'all' || e.agent === selectedModel,
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
  const tickerItems = useMemo(() => {
    // A股实盘：优先滚动实盘持仓实时价（桥 quote）
    if (market === 'cn' && livePositions.length > 0) {
      return livePositions
        .filter((p) => Number(p.last_price) > 0)
        .map((p) => ({
          sym: p.stock_code,
          name: p.name,
          quote: {
            price: Number(p.last_price),
            date: '',
            prev_close: Number(p.cost_price),
            // 后端 pnl_pct 是百分数(1.43), fmtPct 期望小数(0.0143) → 除 100
            change_pct: Number(p.pnl_pct) / 100,
          },
        }));
    }
    return heldSymbols
      .map((sym) => ({ sym, quote: prices.data?.[sym] ?? null, name: stockNames.data?.[sym] }))
      .filter((t) => t.quote != null);
  }, [market, livePositions, heldSymbols, prices.data, stockNames.data]);

  // ---------- 事件流（成交，/trades 顶层字段） ----------
  const tradeEvents: TradeEvt[] = useMemo(
    () =>
      (trades.data ?? [])
        .map((r) => ({
          date: r.date,
          side: (r.action ?? '').toLowerCase() === 'buy' ? 'buy' as const : 'sell' as const,
          symbol: r.symbol,
          name: stockNames.data?.[r.symbol] ?? r.symbol,
          amount: r.amount,
          cash: r.cash_after ?? 0,
          price: r.price ?? null,
          notional: r.notional ?? null,
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
    [trades.data, stockNames.data],
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
      return <CompConfigPanel models={rows.map((r) => r.name)} />;
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
      const entries = Object.entries(last?.positions ?? {}).filter(([sym]) => sym !== 'CASH');
      const cash = Number(last?.positions?.CASH ?? 0);
      // A股实盘持仓（通达信桥）置顶展示；按选中模型筛选（'all' = 全部）
      if (market === 'cn' && livePositions.length > 0) {
        const ag =
          selectedModel !== 'all' ? (liveLedger.data?.agents?.[selectedModel] ?? null) : null;
        const mineCodes = ag ? new Set(ag.positions.map((lp) => lp.code)) : null;
        const shownPositions = mineCodes
          ? livePositions.filter((p) => mineCodes.has(p.stock_code))
          : livePositions;
        return (
          <div style={{ padding: '8px 12px' }}>
            <div className="pos-section-title">
              {ag ? `模型 ${selectedModel} 名下持仓` : '实盘持仓（通达信桥）'}
              <span className="pos-section-sub">
                {ag
                  ? `${shownPositions.length} 只 · 额度已用 ¥${ag.used.toLocaleString('en-US')} / ¥${ag.quota.toLocaleString('en-US')}`
                  : fmtMoney(liveAcct.data?.asset ?? 0, meta.currency)}
              </span>
            </div>
            {shownPositions.length === 0 && (
              <div className="empty-state" style={{ padding: '12px 0' }}>
                {ag ? '该模型名下暂无实盘持仓' : '暂无持仓'}
              </div>
            )}
            {shownPositions.map((p) => (
              <div className="live-pos-card" key={p.stock_code}>
                <div className="live-pos-main">
                  <span className="live-pos-name">{p.name}</span>
                  <span className="live-pos-code">{p.stock_code}</span>
                  <span className={`live-pos-pnl ${Number(p.pnl) >= 0 ? 'up' : 'down'}`}>
                    {Number(p.pnl) >= 0 ? '+' : ''}{fmtMoney(Number(p.pnl), meta.currency)}
                    {' '}({Number(p.pnl_pct) >= 0 ? '+' : ''}{Number(p.pnl_pct).toFixed(2)}%)
                  </span>
                </div>
                <div className="live-pos-sub">
                  <span>买入 {p.buy_time.slice(5)}</span>
                  <span>{Number(p.total_volume).toLocaleString('en-US')} 股</span>
                  <span>成本 {fmtMoney(Number(p.cost_price), meta.currency)}</span>
                  <span>现价 {fmtMoney(Number(p.last_price), meta.currency)}</span>
                  <span>持仓 {fmtMoney(Number(p.position_value), meta.currency)}</span>
                </div>
              </div>
            ))}
            <div className="pos-section-title" style={{ marginTop: 14 }}>模拟盘持仓</div>
            <div className="pos-row">
              <span className="pos-sym">现金 CASH</span>
              <span className="pos-cash">{fmtMoney(cash, meta.currency)}</span>
            </div>
            {entries.length === 0 && (
              <div className="empty-state" style={{ padding: '24px 0' }}>空仓 — 无持仓</div>
            )}
            {entries.map(([sym, qty]) => (
              <div className="pos-row" key={sym}>
                <span className="pos-sym">
                  <span className="pos-name">{stockNames.data?.[sym] ?? sym}</span>
                  <span className="pos-code">{sym}</span>
                </span>
                <span className="pos-qty">{Number(qty).toLocaleString('en-US')}</span>
              </div>
            ))}
          </div>
        );
      }
      if (!last) return <div className="empty-state">暂无持仓数据</div>;
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
              <span className="pos-sym">
                <span className="pos-name">{stockNames.data?.[sym] ?? sym}</span>
                <span className="pos-code">{sym}</span>
              </span>
              <span className="pos-qty">{Number(qty).toLocaleString('en-US')}</span>
            </div>
          ))}
        </div>
      );
    }

    if (tab === 'chat') {
      // 全部模型：混合时间流（不按模型分组，各模型最新分析都排前面）
      if (selectedModel === 'all') {
        if (!chatAll.data) return <div className="empty-state">加载对话…</div>;
        return <ChatStream agents={chatAll.data} />;
      }
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

    // COMPLETED —— 当前市场平仓消息流（nof1 风格；按筛选模型过滤，'all' = 全部）
    if (tab === 'completed') {
      return (
        <CompletedFeed
          agents={selectedModel === 'all' ? rows.map((r) => r.name) : [selectedModel]}
          market={market}
          currency={meta.currency}
          stockNames={stockNames.data ?? {}}
          onCount={setCompletedCount}
        />
      );
    }

    // TRADES —— 原始成交详细卡片（选中模型的全部成交；A股置顶今日实盘成交）
    if (!tradeEvents.length && liveTradeEvents.length === 0) {
      return <div className="empty-state">暂无成交</div>;
    }
    return (
      <>
        {market === 'cn' && liveTradesFiltered.length > 0 && (
          <>
            <div className="pos-section-title">今日实盘成交（通达信桥）</div>
            {liveTradesFiltered.map((e, i) => (
              <div className="trade-card" key={`live-${e.ts}-${i}`}>
                <div className="trade-card-head">
                  <span className="trade-side buy">买入</span>
                  <b className="trade-card-symbol">{e.name}</b>
                  <span className="trade-card-code">{e.code}</span>
                  <span className="trade-card-date">{e.ts.slice(5, 16)}</span>
                </div>
                <div className="trade-card-grid">
                  <span>归属{' '}
                    <b style={{ color: e.agent ? modelColor(e.agent) : '#000' }}>
                      {e.agent ?? '总账户'}
                    </b>
                  </span>
                  <span>数量 <b>{e.volume.toLocaleString('en-US')}</b></span>
                  <span>成交价 <b>{e.price != null ? fmtMoney(e.price, meta.currency) : '—'}</b></span>
                  <span>成交金额 <b>{fmtMoney((e.price ?? 0) * e.volume, meta.currency)}</b></span>
                </div>
              </div>
            ))}
            <div className="pos-section-title" style={{ marginTop: 10 }}>模拟盘成交</div>
          </>
        )}
        {tradeEvents.map((e, i) => (
          <div className="trade-card" key={`${e.date}-${i}`}>
            <div className="trade-card-head">
              <span className={`trade-side ${e.side}`}>{e.side === 'buy' ? '买入' : '卖出'}</span>
              <b className="trade-card-symbol">{e.name}</b>
              <span className="trade-card-code">{e.symbol}</span>
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
                {(perfs.data ?? []).map((p) => {
                  // A股实盘: 模型卡显示实盘分账收益(虚拟净值/¥10万基准), 替代模拟盘回放
                  const eqPts = market === 'cn' ? liveEquity.data?.agents?.[p.agent] : null;
                  const liveNav = eqPts && eqPts.length ? eqPts[eqPts.length - 1].value : null;
                  const isLive = market === 'cn' && liveNav != null;
                  return (
                    <ModelCard
                      key={p.agent}
                      market={market}
                      agent={p.agent}
                      balance={isLive ? liveNav : (p.summary?.end_equity ?? null)}
                      ret={isLive ? (liveNav! / 100000 - 1) * 100 : (p.summary?.total_return ?? null)}
                      selected={p.agent === effectiveModel}
                      onClick={() =>
                        setSelectedModel((cur) => (cur === p.agent ? 'all' : p.agent))
                      }
                      tokens={tokenUsage.data?.agents?.[p.agent] ?? null}
                    />
                  );
                })}
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
            {tab === 'completed' || tab === 'trades' || tab === 'chat' || tab === 'positions' ? (
              <select
                className="filter-select"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
              >
                <option value="all">全部模型</option>
                {rows.map((r) => (
                  <option key={r.name} value={r.name}>{r.name}</option>
                ))}
              </select>
            ) : (
              <span className="filter-static">全部模型</span>
            )}
            <span className="filter-count">
              {tab === 'completed'
                ? completedCount
                : tab === 'trades'
                  ? tradeEvents.length
                  : tab === 'chat'
                    ? selectedModel === 'all'
                      ? (chatAll.data ?? []).reduce((n, a) => n + a.lines.length, 0)
                      : (logs.data ?? []).length
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
