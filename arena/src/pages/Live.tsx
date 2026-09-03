import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  BenchPoint,
  LogLine,
  MarketId,
  OverviewRow,
  PositionRecord,
  TradeRecord,
  fetchBenchmark,
  fetchFutuAccountBoth,
  fetchIndices,
  fetchLiveAccountFor,
  fetchLiveEquity,
  fetchLiveLedger,
  fetchLiveTradesFor,
  fetchLogs,
  fetchOverview,
  fetchTokenUsage,
  triggerLiveAnalysis,
  fetchPerformance,
  fetchPositions,
  fetchPrices,
  fetchStockNames,
  fetchTrades,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { toBenchLine, toChartLine, HoldingSpan } from '../components/EquityChart';
import RealAccountPanel from '../components/RealAccountPanel';
import ModelCard, { modelColor, shortName } from '../components/ModelCard';
import ChatStream from '../components/ChatStream';
import NewsStream from '../components/NewsStream';
import CompletedFeed from '../components/CompletedFeed';
import CompConfigPanel from '../components/CompConfigPanel';
import { MarketSwitcher } from '../components/Navbar';
import { fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Live.css';

const BENCH_COLOR = '#10a37f';

/** 空仓时间段反推（成交事件 + 当前账本，时间戳毫秒；纯事实驱动——
 *  净值曲线本身是阶梯状（桥行情缓存分钟级刷新），按数值连段判空仓会整线误虚。
 *  账本只存当前快照，用成交事件倒走重建：穿越一笔清仓卖出 → 空仓区间开始；
 *  穿越一笔买入 → 空仓区间结束。无任何事件且现空仓 → 视为自数据起点空仓。 */
function emptyTsIntervals(
  events: { ts: string; agent?: string | null; code?: string; side?: string; volume?: number }[],
  ledgerAgents: Record<string, { positions?: { code: string; volume: number }[] }>,
): Record<string, [number, number][]> {
  const out: Record<string, [number, number][]> = {};
  const nowTs = Date.now();
  for (const [agent, rec] of Object.entries(ledgerAgents ?? {})) {
    const qty = new Map<string, number>();
    for (const p of rec?.positions ?? []) qty.set(p.code, p.volume);
    const total = () => [...qty.values()].reduce((s, v) => s + v, 0);
    const evts = (events ?? [])
      .filter((e) => e.agent === agent && e.side && Number(e.volume) > 0)
      .sort((a, b) => (a.ts < b.ts ? 1 : -1));
    if (!evts.length) {
      // 无成交事件：持仓状态即当前账本。空仓起点交给 trailingFlatFrom 兜底
      // （净值最后变动时刻），此处不制造区间
      continue;
    }
    const intervals: [number, number][] = [];
    let emptyEnd: number | null = null;
    for (const e of evts) {
      const t = new Date(e.ts).getTime();
      const stateAfter = total() > 0;
      const code = e.code ?? '';
      const vol = Number(e.volume) || 0;
      if (String(e.side).toLowerCase() === 'sell') qty.set(code, (qty.get(code) ?? 0) + vol);
      else qty.set(code, Math.max(0, (qty.get(code) ?? 0) - vol));
      const stateBefore = total() > 0;
      if (!stateAfter && stateBefore && code) {
        // 倒走穿越清仓卖出：此刻（正向）刚卖光 → 空仓区间起点
        intervals.push([t, emptyEnd ?? nowTs]);
      } else if (stateAfter && !stateBefore) {
        // 倒走穿越买入：此刻（正向）刚买回 → 空仓区间终点
        emptyEnd = t;
      }
    }
    if (total() === 0 && intervals.length === 0) {
      // 倒走到底仍空仓（历史无买入记录）→ 自数据起点空仓
      intervals.push([0, emptyEnd ?? nowTs]);
    }
    out[agent] = intervals;
  }
  return out;
}

/** 空仓时间区间 → 该 agent 净值序列中需画虚线的下标段（含两端）。
 *  groups 支持多组区间取并集（事件反推 + 空仓尾段兜底）。 */
function tsToDashSegs(
  pts: { t: number }[],
  groups: [number, number][][],
): [number, number][] {
  const all = groups.flat();
  if (!all.length) return [];
  const segs: [number, number][] = [];
  let cur: [number, number] | null = null;
  pts.forEach((p, idx) => {
    const inEmpty = all.some(([a, b]) => p.t >= a && p.t <= b);
    if (inEmpty && !cur) cur = [idx, idx];
    else if (inEmpty && cur) cur[1] = idx;
    else if (!inEmpty && cur) {
      segs.push(cur);
      cur = null;
    }
  });
  if (cur) segs.push(cur);
  return segs;
}

/** 净值序列中最后一个值变动点的下标（其后全部同值）→ 空仓尾段的起始。
 *  事件缺失（旧路径成交未留 fill 记录）时用净值本身的变动史兜底：
 *  当前空仓 → 从最后一次变动起虚线（变动前是有仓位的实线）。 */
function trailingFlatFrom(vals: number[]): number {
  for (let i = vals.length - 1; i >= 1; i--) {
    if (Math.abs(vals[i] - vals[i - 1]) > 1e-9) return i;
  }
  return 0;
}

/** 持仓时间线（悬停补充）：按 agent/代码 从当前账本出发倒走成交事件，
 *  重建每段持仓的 (from,to] 毫秒区间与数量；当前持仓以 buy_ts 为起点，
 *  已清仓的股票区间只由事件支撑，历史未知段不臆造。 */
function holdingsTimelineOf(
  events: { ts: string; agent?: string | null; side?: string; volume?: number; code?: string }[],
  ledgerAgents: Record<string, { positions?: { code: string; volume: number }[] }>,
): HoldingSpan[] {
  const spans: HoldingSpan[] = [];
  const now = Date.now();
  for (const [agent, rec] of Object.entries(ledgerAgents ?? {})) {
    const pos = rec?.positions ?? [];
    const qty = new Map<string, number>();
    const buyTs = new Map<string, number>();
    for (const p of pos) {
      qty.set(p.code, p.volume);
      const bt = new Date((p as { buy_ts?: string }).buy_ts ?? '').getTime();
      buyTs.set(p.code, Number.isFinite(bt) ? bt : 0);
    }
    const evts = (events ?? [])
      .filter((e) => e.agent === agent && e.side && Number(e.volume) > 0 && e.code)
      .sort((a, b) => (a.ts < b.ts ? 1 : -1));
    let prevT = now;
    for (const e of evts) {
      const t = new Date(e.ts).getTime();
      const code = e.code ?? '';
      const v = qty.get(code) ?? 0;
      if (t < prevT && v > 0) spans.push({ agent, code, vol: v, from: t, to: prevT });
      // 倒走复原：卖出前持有更多，买入前持有更少
      if (String(e.side).toLowerCase() === 'sell') qty.set(code, v + (Number(e.volume) || 0));
      else qty.set(code, Math.max(0, v - (Number(e.volume) || 0)));
      prevT = t;
    }
    // 事件之前仍持有的（当前账本有 buy_ts）→ 从买入时刻起
    for (const [code, v] of qty) {
      if (v <= 0) continue;
      const from = buyTs.get(code);
      spans.push({ agent, code, vol: v, from: from ?? 0, to: prevT });
    }
  }
  return spans;
}

/** 每秒自走北京时间钟——独立 state，不波及 Live 父组件的轮询/memo。 */
function LiveClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const s = new Date(now + 8 * 3600000).toISOString();
  return <span className="live-clock">{s.slice(0, 10)} {s.slice(11, 19)}</span>;
}

type Tab = 'completed' | 'trades' | 'chat' | 'news' | 'positions' | 'comp' | 'real' | 'details';
type TimeRange = 'all' | '5d';

/** 右侧 tab：已完成交易 / 成交 / 模型对话 / 新闻 / 持仓 / 比赛配置 / 详情 */
const TABS: { id: Tab; label: string }[] = [
  { id: 'completed', label: '已完成' },
  { id: 'trades', label: '成交' },
  { id: 'chat', label: '模型对话' },
  { id: 'news', label: '新闻' },
  { id: 'positions', label: '持仓' },
  { id: 'comp', label: '比赛配置' },
  { id: 'real', label: '实盘' },
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
  agent?: string | null; // 模拟盘成交归属（all 视图多模型聚合时标注）
}

// ---------- 市场交易时段（北京时间）与交易规则 ----------

/** 美股交易时段随夏令时切换（美国 3 月第二个周日 ~ 11 月第一个周日）。 */
const usDstActive = (d: Date): boolean => {
  const y = d.getFullYear();
  const secondSun = (m: number) => {
    const x = new Date(y, m, 1);
    while (x.getDay() !== 0) x.setDate(x.getDate() + 1);
    x.setDate(x.getDate() + 7);
    return x;
  };
  return d >= secondSun(2) && d < secondSun(10);
};

const MARKET_HOURS: Record<MarketId, { rule: string }> = {
  cn: { rule: 'T+1 · 主板 ±10% 涨跌停' },
  hk: { rule: 'T+0 · 无涨跌停' },
  us: { rule: 'T+0 · 无涨跌停' },
};

/** 交易时段标签（北京时间）：US 按当天是否夏令时切换。 */
const hoursLabelOf = (market: MarketId, now: Date): string => {
  if (market === 'cn') return '09:30–11:30 / 13:00–15:00';
  if (market === 'hk') return '09:30–12:00 / 13:00–16:00';
  return usDstActive(now) ? '夏令时 21:30–04:00(次日)' : '冬令时 22:30–05:00(次日)';
};

/** 当前盘中状态（北京时间）：盘前/盘中/中午休息/盘后/休市。 */
const marketStatusOf = (market: MarketId, now: Date): { text: string; open: boolean } => {
  const bj = new Date(now.getTime() + (480 + now.getTimezoneOffset()) * 60000); // 东八区（tzOffset 东负西正，如 JST=-540 → -60min）
  const mins = bj.getHours() * 60 + bj.getMinutes();
  const wd = bj.getDay();
  if (wd === 0 || wd === 6) return { text: '休市', open: false };
  const inRange = (a: number, b: number) => mins >= a && mins < b;
  if (market === 'cn') {
    if (inRange(9 * 60, 9 * 60 + 30)) return { text: '盘前', open: false }; // 集合竞价 9:15 前也算盘前
    if (inRange(9 * 60 + 30, 11 * 60 + 30) || inRange(13 * 60, 15 * 60)) return { text: '盘中', open: true };
    if (inRange(11 * 60 + 30, 13 * 60)) return { text: '中午休息', open: false };
    return { text: '盘后', open: false };
  }
  if (market === 'hk') {
    if (inRange(9 * 60, 9 * 60 + 30)) return { text: '盘前', open: false }; // 开市前竞价 9:00-9:30
    if (inRange(9 * 60 + 30, 12 * 60) || inRange(13 * 60, 16 * 60)) return { text: '盘中', open: true };
    if (inRange(12 * 60, 13 * 60)) return { text: '中午休息', open: false };
    return { text: '盘后', open: false };
  }
  // US（北京时间）：盘前 = 美东 04:00–09:30
  const preOpen = usDstActive(bj) ? 16 * 60 : 15 * 60; // 夏令时 16:00 / 冬令时 15:00
  const openAt = usDstActive(bj) ? 21 * 60 + 30 : 22 * 60 + 30;
  const closeAt = usDstActive(bj) ? 4 * 60 : 5 * 60; // 次日凌晨（北京时间）
  if (mins >= openAt || mins < closeAt) return { text: '盘中', open: true };
  if (mins >= preOpen && mins < openAt) return { text: '盘前', open: false };
  return { text: '盘后', open: false };
};

const benchLabelOf = (market: MarketId): string =>
  market === 'us' ? 'NDX100' : market === 'cn' ? 'SSE50' : 'HSI';

/** Live 终端页 —— 终端风布局：
 *  顶部价格条 + HIGHEST/LOWEST → 左净值图 + 模型横排卡 → 右 540px 七 tab 面板 */
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

  const [tab, setTab] = useState<Tab>('chat'); // 默认=模型对话（用户口径）
  const [chartRange, setChartRange] = useState<TimeRange>('all');
  const [chartMode, setChartMode] = useState<'pct' | 'dollar'>('pct');
  const [selectedModel, setSelectedModel] = useState<string>('all');
  // 「立即分析」手动触发状态（对话 tab 筛选栏按钮）
  const [analyzeState, setAnalyzeState] = useState<'idle' | 'busy' | 'sent' | 'error'>('idle');
  const [analyzeMsg, setAnalyzeMsg] = useState('');
  const runManualAnalysis = async () => {
    setAnalyzeState('busy');
    setAnalyzeMsg('');
    try {
      await triggerLiveAnalysis(selectedModel === 'all' ? 'all' : [selectedModel]);
      setAnalyzeMsg(
        `已触发${selectedModel === 'all' ? '全部分账模型' : ` ${selectedModel}`}分析，约 1 分钟内开跑`,
      );
      setAnalyzeState('sent');
      window.setTimeout(() => setAnalyzeState('idle'), 25000);
    } catch (e) {
      setAnalyzeMsg(`触发失败：${e instanceof Error ? e.message : String(e)}`);
      setAnalyzeState('error');
    }
  };
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
  // 当日实时指数（顶部行情条）：CN 桥日K 6 指数 / US NDX100 基准 / HK 空
  const indices = usePolling(() => fetchIndices(market), [market], 30000);

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
  // 实盘账本/成交（上移：空仓段反推在 lines memo 里要用）
  const liveLedger = usePolling(() => fetchLiveLedger(), [], 30000);
  const liveTrades = usePolling(() => fetchLiveTradesFor(market), [market], 15000);

  const lines = useMemo(() => {
    const eq = liveEquity.data;
    // 实盘采样必须用完整时间戳 ts（含时刻），date 只是 YYYY-MM-DD 会把当天所有点挤到零点
    const toEq = (v: number, ts: string) => ({ date: ts, cash: 0, market_value: 0, equity: v });
    // A股实盘优先：每 agent 分账虚拟净值线（¥10 万起，通达信桥实时价）
    // + 总账户线（桥实时总资产）。序列 ≥2 点才画。
    if (market === 'cn' && eq) {
      // 空仓段虚线：成交事件反推 + （无事件时）净值最后变动点兜底
      const emptyTs = emptyTsIntervals(
        (liveTrades.data ?? []) as unknown as Parameters<typeof emptyTsIntervals>[0],
        (liveLedger.data?.agents ?? {}) as unknown as Parameters<typeof emptyTsIntervals>[1],
      );
      const emptyNowAgents = new Set(
        Object.entries(liveLedger.data?.agents ?? {})
          .filter(([, rec]) => !(rec?.positions ?? []).length)
          .map(([a]) => a),
      );
      // 分账线：仅空仓那一段虚线（保留信息量），持仓段一律实线
      const agentLines = Object.entries(eq.agents ?? {})
        .filter(([, pts]) => pts.length >= 2)
        .map(([name, pts]) => {
          const line = toChartLine(
            `live-${name}`,
            name,
            modelColor(name),
            pts.map((e) => toEq(e.value, e.ts)),
          );
          const groups: [number, number][][] = [emptyTs[name] ?? []];
          if (emptyNowAgents.has(name)) {
            // 现空仓且无（或已有）事件：净值最后变动点之后 = 空仓尾段
            const from = trailingFlatFrom(pts.map((e) => e.value));
            groups.push([[line.points[from].t, Date.now()]]);
          }
          return {
            ...line,
            notional: 100000, // 分账名义基准: hover 换算金额盈亏
            dashSegs: tsToDashSegs(line.points, groups),
          };
        });
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
  }, [perfs.data, liveEquity.data, liveLedger.data, liveTrades.data, market]);

  // 悬停补充（时序事实）：当时持仓时间线 + 成交事件（仅 cn 实盘数据可支撑）
  const heldSpans = useMemo(
    () =>
      market === 'cn'
        ? holdingsTimelineOf(
            liveTrades.data as unknown as Parameters<typeof holdingsTimelineOf>[0],
            liveLedger.data?.agents as unknown as Parameters<typeof holdingsTimelineOf>[1],
          )
        : [],
    [market, liveTrades.data, liveLedger.data],
  );
  // 当前单价（持仓金额估算用）：账本 position_value/volume（桥实时价口径）
  const holderPriceMap = useMemo(() => {
    const m: Record<string, number> = {};
    for (const rec of Object.values(liveLedger.data?.agents ?? {})) {
      for (const p of (rec as { positions?: { code: string; volume: number; position_value?: number }[] }).positions ?? []) {
        if (p.position_value != null && p.volume > 0) {
          m[p.code] = Number(p.position_value) / p.volume;
        }
      }
    }
    return m;
  }, [liveLedger.data]);

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
    () =>
      effectiveModel
        ? selectedModel === 'all'
          ? Promise.all(
              rows.map((r) =>
                fetchTrades(r.name, market).catch(() => [] as TradeRecord[]),
              ),
            ).then((lists) =>
              lists.flatMap((list, i) =>
                list.map((t) => ({ ...t, agent: rows[i]?.name ?? null })),
              ),
            )
          : fetchTrades(effectiveModel, market)
        : Promise.resolve([]),
    [effectiveModel, selectedModel, rows, market],
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
    const units = rows.map((r) => ({ name: r.name, pull: () => fetchLogs(r.name, market) }));
    if (market === 'cn') {
      // 晚间市场研究 agent 的对话卡（pseudo『研究总控』，数据目录 market-research）
      units.push({ name: '研究总控', pull: () => fetchLogs('market-research', market) });
    }
    return Promise.all(units.map((u) => u.pull().catch(() => [] as LogLine[]))).then(
      (lists) => units.map((u, i) => ({ name: u.name, lines: lists[i] })),
    );
  }, [selectedModel, tab, rows, market], 30000);

  // ---------- 实盘账户（A股：通达信桥 /live/account；港股：富途 /api/futu/account 直连 OpenD） ----------
  const liveAcct = usePolling(() => fetchLiveAccountFor(market), [market], 15000);
  const livePositions = (liveAcct.data?.positions ?? []).filter(
    (p) => Number(p.total_volume) > 0,
  );
  // 港股实盘 tab 双卡（富途 REAL+SIMULATE，一次握手游走）后台 15s 轮询 → 点击 tab 即见，
  // 不在 RealAccountPanel 内单独起子进程（省一次 ~4s RSA 握手）
  const futuBoth = usePolling(
    () => (market === 'hk' ? fetchFutuAccountBoth() : Promise.resolve(null)),
    [market],
    15000,
  );
  // 实盘总浮盈（桥实时价驱动，随 liveAcct 每 15s 刷新）
  const totalPnl = useMemo(
    () => livePositions.reduce((s, p) => s + Number(p.pnl ?? 0), 0),
    [livePositions],
  );
  // 实盘分账账本（每 agent ¥10 万虚拟子账户，按模型显示各自持仓）
  // （hook 上移：空仓虚线判定在 lines memo 里要用）

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
    .filter((t) => {
      // 新格式：wait_fill 成交回报（fill 字段）；旧格式：result.status
      const hasFill = t.fill && Number(t.fill.filled_volume) > 0;
      const hasResult =
        t.result &&
        typeof t.result.status === 'string' &&
        t.result.status !== 'rejected' &&
        !String(t.result.message ?? '').includes('签名');
      return hasFill || hasResult;
    })
    .map((t) => ({
      ts: t.ts,
      code: t.code,
      side: t.side,
      volume: t.volume,
      price: t.price ?? null,
      name: (t.name || stockNames.data?.[t.code]) ?? t.code,
      // 记录自带的 agent 优先（卖出后该股已不在任何账本，当前账本反查会丢归属）
      agent: (t as { agent?: string | null }).agent ?? ledgerHolderOf[t.code] ?? null,
    }))
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
  /** 按选中模型过滤实盘成交（'all' = 全部） */
  const liveTradesFiltered = liveTradeEvents.filter(
    (e) => selectedModel === 'all' || e.agent === selectedModel,
  );
  /** 实盘成交按日期分组（今日 → 9/1 …），日期头分组展示历史 */
  const liveGroups = useMemo(() => {
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    const out: { label: string; rows: typeof liveTradesFiltered }[] = [];
    for (const e of liveTradesFiltered) {
      const d = String(e.ts).slice(0, 10);
      const last = out[out.length - 1];
      if (last && last.rows[0] && String(last.rows[0].ts).slice(0, 10) === d) {
        last.rows.push(e);
      } else {
        out.push({
          label: d === today ? `今日实盘成交 · ${d.slice(5)}` : `实盘成交 · ${d.slice(5)}`,
          rows: [e],
        });
      }
    }
    return out;
  }, [liveTradesFiltered]);
  const heldSymbols = useMemo(() => {
    const set = new Set<string>();
    for (const rec of marketPositions.data ?? []) {
      for (const [sym, qty] of Object.entries(rec.positions ?? {})) {
        if (sym !== 'CASH' && Number(qty) > 0) set.add(sym);
      }
    }
    return [...set];
  }, [marketPositions.data]);
  /** 新闻 tab 关注列表 = 实盘分账持仓 + 模拟盘持仓（A股代码格式与 quantmind enrichment 一致） */
  const newsTickers = useMemo(() => {
    const set = new Set<string>(heldSymbols);
    for (const rec of Object.values(liveLedger.data?.agents ?? {})) {
      for (const p of rec.positions ?? []) set.add(p.code);
    }
    // 实盘持仓代码（A股通达信 / 港股富途）也纳入新闻关注
    for (const p of livePositions) set.add(p.stock_code);
    return [...set];
  }, [heldSymbols, liveLedger.data, livePositions]);
  // 港股新闻关键词：富途无 HK 标签文章库，改用持仓短名（腾讯控股→腾讯）全文搜；
  // 无持仓则用「港股」泛搜恒生/港交所等。A股走 tickers 不用 keyword。
  const hkNewsKeyword = useMemo(() => {
    if (market !== 'hk') return '';
    const top = livePositions
      .slice()
      .sort((a, b) => Number(b.position_value) - Number(a.position_value))[0];
    if (!top?.name) return '港股';
    const short = top.name
      .replace(/(控股|实业|集团|股份.*|有限公司|Limited|Inc\.?|Corp\.?)$/i, '')
      .trim();
    return short || '港股';
  }, [market, livePositions]);
  const tickerItems = useMemo(() => {
    // 实盘：优先滚动实盘持仓实时价（A股通达信桥 / 港股富途，livePositions 同 shape）
    if ((market === 'cn' || market === 'hk') && livePositions.length > 0) {
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
          agent: (r as { agent?: string | null }).agent ?? null,
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
    [trades.data, stockNames.data],
  );
  /** 模拟盘成交按选中模型过滤（'all' = 全部，事件已带 agent 标注） */
  const tradeEventsFiltered = tradeEvents.filter(
    (e) => selectedModel === 'all' || e.agent === selectedModel,
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
      return <CompConfigPanel models={rows.map((r) => r.name)} market={market} />;
    }

    if (tab === 'real') {
      return <RealAccountPanel market={market} currency={meta.currency} futuBoth={futuBoth.data} />;
    }

    if (tab === 'details') {
      return (
        <div className="readme-body">
          <h4>① 系统流水线（数据 → 决策 → 执行）</h4>
          <p>
            <b>行情层</b>：通达信桥实时快照（现价/五档/盘口失衡/隔夜跳空/5 分钟涨速/量比）
            + QuantDB 全市场日线/财报/板块/因子库（每晚收盘入库）+ L2 逐笔微观因子（盘中采集）。
          </p>
          <p>
            <b>分析层</b>：每 agent 按所选模式（基线/苦行/情境感知/极限杠杆）逐只简评——
            输入=持仓表+盘面状态+情绪温度+近期成交回顾+新闻情绪，输出=四段式
            （总体总结/分析链路/推理论证/JSON 决策）。
          </p>
          <p>
            <b>执行层</b>：决策 JSON → 系统闸门校验 → 通达信桥下单（限价）→ 成交回报确认 →
            分账账本记账。watch 决策挂分钟级价格哨兵条件位。
          </p>
          <h4>② 智能体阵容（A股实盘分账，每模型 ¥10 万虚拟额度）</h4>
          <p>
            <b>v4-flash</b>：dsh 工具型 agent（行情/quantdb/搜索/记忆/数学 MCP 工具+可写代码），
            带 1-2 分钟时间盒工作法、时段作战手册与大盘剧本。
          </p>
          <p>
            <b>v4-pro / glm</b>：直连 LLM 分析（同数据注入、同决策 schema、同风控闸门）。
          </p>
          <h4>③ 风控闸门（系统侧强制，模型不可绕过）</h4>
          <p>
            T+1 可卖量复核 · 单票 ≤ 剩余额度 20% · 持仓市值 ≤ 权益 ×1.5（超线分钟级守护自动减仓）·
            涨停不追/跌停不接 · 分账额度不透支 · 拒单自动登记延期单并在行情恢复后重放 ·
            行情停更硬闸（停更期间禁止基于价格的交易决策）。
          </p>
          <h4>④ 决策与记账口径</h4>
          <p>
            决策必带：理由 + 止损 + 止盈 + 移动止损 + 失效条件 + 置信度 + 风险额；
            缺退出框架的买卖决策作废。成交按桥回报确认后入账，「已完成」为真实清仓流。
          </p>
          <h4>⑤ 技能与工具（16 个）</h4>
          <p>
            个股全维体检 / 实时行情直读 / 新闻情绪 / 市场情绪报告 / 大盘研报 / 复盘选股 /
            深度研究 / 富途 / IBKR / 老虎 / quantdb 字段手册…… 详见 docs/SKILLPACK.md。
          </p>
          <h4>⑥ 数据口径与刷新</h4>
          <p>
            页面 15-30 秒自动刷新；行情/持仓/成交/清仓实时重建；净值分钟级采样
            （数据更新至 {rows[0]?.latest_date ?? '—'}）；情绪温度为昨日收盘全景（盘前/盘后参考）。
          </p>
        </div>
      );
    }

    if (tab === 'positions') {
      const last = positions.data?.[positions.data.length - 1];
      const entries = Object.entries(last?.positions ?? {}).filter(([sym]) => sym !== 'CASH');
      const cash = Number(last?.positions?.CASH ?? 0);
      // 实盘持仓置顶展示（A股通达信桥 / 港股富途，同 shape）；按选中模型筛选（'all' = 全部）
      // 港股富途是单一共享账户，无 A 股分账（每模型 ¥10 万子账户）体系 → 不按模型筛
      if ((market === 'cn' || market === 'hk') && livePositions.length > 0) {
        const ag =
          market === 'cn' && selectedModel !== 'all'
            ? (liveLedger.data?.agents?.[selectedModel] ?? null)
            : null;
        const mineCodes = ag ? new Set(ag.positions.map((lp) => lp.code)) : null;
        const shownPositions = mineCodes
          ? livePositions.filter((p) => mineCodes.has(p.stock_code))
          : livePositions;
        return (
          <div style={{ padding: '8px 12px' }}>
            <div className="pos-section-title">
              {ag ? `模型 ${selectedModel} 名下持仓` : market === 'hk' ? '实盘持仓（富途模拟）' : '实盘持仓（通达信桥）'}
              <span className="pos-section-sub">
                {ag
                  ? `${shownPositions.length} 只 · 额度已用 ¥${ag.used.toLocaleString('en-US')} / ¥${ag.quota.toLocaleString('en-US')}`
                  : `总浮盈 ${totalPnl >= 0 ? '+' : ''}${fmtMoney(totalPnl, meta.currency)} · 总资产 ${fmtMoney(liveAcct.data?.asset ?? 0, meta.currency)}`}
              </span>
              <LiveClock />
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
            {market !== 'hk' && (
              <>
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
              </>
            )}
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
      // 统一用 ChatStream：'all' = 各模型混合时间流；筛选单模型 = 同组件
      // 只喂该模型（界面与「全部」一致，仅数据收窄）
      const agents =
        selectedModel === 'all'
          ? chatAll.data
          : [{ name: effectiveModel, lines: logs.data ?? [] }];
      if (!agents) return <div className="empty-state">加载对话…</div>;
      return (
        <ChatStream
          agents={agents}
          fills={liveTradesFiltered}
          heldCodes={new Set(livePositions.map((p) => p.stock_code))}
        />
      );
    }

    if (tab === 'news') {
      return (
        <NewsStream
          tickers={market === 'cn' ? newsTickers : []}
          hours={12}
          limit={30}
          keyword={hkNewsKeyword}
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
    if (!tradeEventsFiltered.length && liveTradesFiltered.length === 0) {
      return <div className="empty-state">暂无成交</div>;
    }
    return (
      <>
        {(market === 'cn' || market === 'hk' || market === 'us') && liveGroups.length > 0 && (
          <>
            {liveGroups.map((g) => (
              <div key={g.label}>
                <div className="pos-section-title">
                  {g.label}
                  {g.rows[0] && (market === 'hk' ? '（富途）' : market === 'us' ? '（IBKR）' : '（通达信桥）')}
                </div>
                {g.rows.map((e, i) => {
                  const isSell = String(e.side ?? '').toUpperCase() === 'SELL';
                  const isBuy = String(e.side ?? '').toUpperCase() === 'BUY';
                  // 卖出口径区分：卖后桥仍持有该股 → 减仓；已不持有 → 清仓
                  const heldNow = livePositions.some((p) => p.stock_code === e.code);
                  const sideLabel = !isBuy && !isSell ? '成交' : isBuy ? '买入' : heldNow ? '减仓' : '清仓';
                  return (
                    <div className="trade-card" key={`live-${e.ts}-${i}`}>
                      <div className="trade-card-head">
                        <span
                          className={`trade-side ${
                            isBuy ? 'buy' : isSell ? (heldNow ? 'sell partial' : 'sell') : 'info'
                          }`}
                        >
                          {sideLabel}
                        </span>
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
                  );
                })}
              </div>
            ))}
            <div className="pos-section-title" style={{ marginTop: 10 }}>模拟盘成交</div>
          </>
        )}
        {tradeEventsFiltered.map((e, i) => (
          <div className="trade-card" key={`${e.date}-${i}`}>
            <div className="trade-card-head">
              <span className={`trade-side ${e.side}`}>{e.side === 'buy' ? '买入' : '卖出'}</span>
              {e.agent && (
                <span className="mc-mode-chip" style={{ marginLeft: 0, marginRight: 6 }}>
                  {shortName(e.agent ?? '')}
                </span>
              )}
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
    <>
      {/* 导航栏横线正下方的独立条：交易时段（北京时间）+ 交易规则 + 盘中状态 +
          最高/最低表演者。切换市场时随市场更新 */}
      <div className="mh-bar">
        {/* 左：交易时段 + 规则 + 状态 + 最高/最低（单行，窄屏横向滚动） */}
        <div className="mh-content">
        {(() => {
          const st = marketStatusOf(market, new Date());
          return (
            <>
              <span className="mh-label">交易时间(北京)</span>
              <span className="mh-hours">{hoursLabelOf(market, new Date())}</span>
              <span className={`mh-status ${st.open ? 'open' : 'closed'}`}>{st.text}</span>
              <span className="mh-divider">·</span>
              <span className="mh-rule">{MARKET_HOURS[market].rule}</span>
              <span className="mh-divider">·</span>
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
            </>
          );
        })()}
        </div>
        {/* 右：市场切换 chips（固定靠右） */}
        <MarketSwitcher market={market} onChange={switchMarket} />
      </div>
      <div className="live">
        {/* 顶部状态条：当日实时指数(2×3) + 市场切换 */}
        <div className="top-status-bar">
          <div className="status-group">
            {indices.data?.indices?.length ? (
              <div className="index-bar">
                {indices.data.indices.map((q) => (
                  <div className="index-item" key={q.code}>
                    <span className="index-name">{q.name}</span>
                    <span className="index-last">
                      {q.last.toLocaleString('en-US', { maximumFractionDigits: 2 })}
                    </span>
                    <span className={`index-chg ${pnlClass(q.change_pct / 100)}`}>
                      {fmtPct(q.change_pct / 100)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="price-item">
                <span className="price-label">{benchLabelOf(market)} 指数</span>
                <span className="price-value">{benchStats.last != null ? fmtMoney(benchStats.last) : '—'}</span>
                <span className={`price-change ${benchStats.dayChange != null ? pnlClass(benchStats.dayChange) : 'dim'}`}>
                  {benchStats.dayChange != null ? fmtPct(benchStats.dayChange) : '无行情'}
                </span>
              </div>
            )}
          </div>
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
                // 悬停时序补充：当时持仓（账本反推时间线）+ 附近 ±3 分钟成交（cn 实盘）
                events={market === 'cn' ? (liveTradesFiltered as unknown as import('../components/EquityChart').HoverEvent[]) : undefined}
                holdings={market === 'cn' && heldSpans.length ? heldSpans : undefined}
                names={stockNames.data ?? undefined}
                priceMap={market === 'cn' && holderPriceMap ? holderPriceMap : undefined}
                mode={chartMode}
                timeRange={chartRange}
                height="clamp(360px, 44vw, 560px)"
              />
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
                      // fmtPct 期望小数（内部 ×100）；这里只算净值/¥10万 的比率
                      ret={isLive ? liveNav! / 100000 - 1 : (p.summary?.total_return ?? null)}
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

        {/* 右：540px 面板 */}
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
            <span className="filter-label">{tab === 'news' ? '关注' : '模型'}</span>
            {tab === 'completed' || tab === 'trades' || tab === 'chat' || tab === 'positions' ? (
              <>
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
              {tab === 'chat' && (
                <>
                  <button
                    className={`analyze-trigger ${analyzeState === 'busy' ? 'busy' : ''}`}
                    disabled={analyzeState === 'busy'}
                    onClick={() => void runManualAnalysis()}
                    title="立即跑一轮完整分析（交易时段内与整点同权，可真下单；盘外只出决策）"
                  >
                    {analyzeState === 'busy' ? '提交中…' : '⚡ 立即分析'}
                  </button>
                  {analyzeMsg && <span className="analyze-msg">{analyzeMsg}</span>}
                </>
              )}
              </>
            ) : tab === 'news' ? (
              <span className="filter-static">
                {market === 'cn'
                  ? `${newsTickers.length} 只持仓`
                  : market === 'hk'
                    ? `关键词「${hkNewsKeyword}」`
                    : '仅 A/H 股'}
              </span>
            ) : (
              <span className="filter-static">全部模型</span>
            )}
            <span className="filter-count">
              {tab === 'completed'
                ? completedCount
                : tab === 'trades'
                  ? tradeEventsFiltered.length
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
    </>
  );
}
