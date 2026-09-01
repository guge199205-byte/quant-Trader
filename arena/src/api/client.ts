/** Quant Agent Trader 数据层：对接 FastAPI 8091 现有端点。
 *  生产环境经 nginx 同源反代（token 由 nginx 注入），浏览器无需持有 token；
 *  dev 模式直连 vite proxy 到 127.0.0.1:8091（api 鉴权未配置时直接可用）。
 */
import axios from 'axios';

export const api = axios.create({ baseURL: '/api', timeout: 20000 });

// ---------- 后端数据结构（与 api_server.py / agent_data.py 对齐） ----------

export type MarketId = 'us' | 'cn' | 'hk';

export const MARKETS: { id: MarketId; label: string; name: string; currency: string }[] = [
  { id: 'cn', label: 'CN', name: 'A股 · SSE 50', currency: '¥' },
  { id: 'hk', label: 'HK', name: '港股 · 恒指成分', currency: 'HK$' },
  { id: 'us', label: 'US', name: '美股 · NASDAQ 100', currency: '$' },
];

export const marketMeta = (id: MarketId) => MARKETS.find((m) => m.id === id) ?? MARKETS[0];

export interface AgentInfo {
  name: string;
  has_position: boolean;
  has_log: boolean;
  latest_date: string | null;
  total_records: number;
  cash: number | null;
}

/** performance 端点 summary（含 compute_extended_summary 扩展指标） */
export interface Summary {
  start_equity: number;
  end_equity: number;
  total_return: number;
  max_drawdown: number;
  records: number;
  sharpe: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  closed_trades: number | null;
  total_fee: number | null;
  fee_ratio: number | null;
  avg_hold_days: number | null;
  position_time_ratio: number | null;
  biggest_win: number | null;
  biggest_loss: number | null;
  avg_trade_pnl: number | null;
  expectancy: number | null;
  avg_trade_size: number | null;
  median_trade_size: number | null;
  median_hold_days: number | null;
}

export interface EquityPoint {
  date: string;
  cash: number;
  market_value: number;
  equity: number;
  action?: string | null;
}

export interface Performance {
  agent: string;
  points: EquityPoint[];
  summary: Summary;
}

export interface PositionRecord {
  date: string;
  id: number;
  this_action?: { action: string; symbol: string; amount: number; price?: number } | null;
  positions: Record<string, number>;
}

export interface LogLine {
  signature?: string;
  /** 日志写入时间 ISO（后端返回，如 2026-08-31T14:00:28） */
  timestamp?: string;
  new_messages?: { role?: string; content?: string }[];
}

/** /agents/{name}/trades 返回：顶层 action/symbol/amount/cash_after（price/notional 由后端重算） */
export interface TradeRecord {
  date: string;
  action: string; // 'buy' | 'sell'
  symbol: string;
  amount: number;
  cash_after: number;
  price: number | null;
  notional: number | null;
}

export interface OverviewRow {
  name: string;
  latest_date: string | null;
  records: number;
  cash: number | null;
  summary: Summary | null;
}

export interface Overview {
  markets: Record<MarketId, OverviewRow[]>;
}

/** /api/metrics：服务健康 + 各市场统计 + 最近交易时间 */
export interface Metrics {
  services: Record<string, 'up' | 'down'>;
  markets?: Record<MarketId, { agents: number; position_records: number; memory_lines: number }>;
  latest_trade_age_sec: number | null;
  generated_at?: number;
}

export const SERVICE_NAMES: Record<string, string> = {
  api: 'API',
  mcp_us: '美股 MCP',
  mcp_cn: 'A股 MCP',
  mcp_hk: '港股 MCP',
  dsh: 'dsh 引擎',
};

// ---------- 端点封装 ----------

/** 解包 {success, data} 信封（先 await 再取 data） */
const unwrap = async <T>(promise: Promise<{ data: { success: boolean; data: T } }>): Promise<T> =>
  (await promise).data.data;

export const fetchOverview = () =>
  unwrap<Overview>(api.get('/overview')).then((d) => d);

export const fetchMetrics = () =>
  unwrap<Metrics>(api.get('/metrics')).then((d) => d);

export const fetchAgents = (market: MarketId) =>
  unwrap<AgentInfo[]>(api.get('/agents', { params: { market } }));

export const fetchPerformance = (agent: string, market: MarketId) =>
  unwrap<Performance>(api.get(`/agents/${encodeURIComponent(agent)}/performance`, { params: { market } }));

export const fetchPositions = (agent: string, market: MarketId) =>
  unwrap<PositionRecord[]>(api.get(`/agents/${encodeURIComponent(agent)}/positions`, { params: { market } }));

export const fetchTrades = (agent: string, market: MarketId) =>
  unwrap<TradeRecord[]>(api.get(`/agents/${encodeURIComponent(agent)}/trades`, { params: { market } }));

export const fetchLogs = (agent: string, market: MarketId) =>
  unwrap<LogLine[]>(api.get(`/agents/${encodeURIComponent(agent)}/logs`, { params: { market } }));

// ---------- 最新价格（滚动价格条） ----------

export interface PriceQuote {
  price: number;
  date: string; // YYYY-MM-DD
  prev_close: number | null;
  change_pct: number | null; // 涨跌幅（昨收基准），无昨收为 null
}

/** 每只股票最新收盘价（键 = symbol，如 "600028.SH"） */
export const fetchPrices = (market: MarketId) =>
  unwrap<Record<string, PriceQuote>>(api.get('/prices', { params: { market } }));

/** 股票中文名表（键 = symbol） */
export const fetchStockNames = (market: MarketId) =>
  unwrap<Record<string, string>>(api.get('/stock-names', { params: { market } }));

// ---------- 通达信桥实盘（A股） ----------

export interface LivePosition {
  stock_code: string; // "600183.SH"
  name: string;
  cost_price: number;
  total_volume: number;
  available_volume: number;
  last_price: number;
  position_value: number;
  pnl_pct: number;
  pnl: number;
  buy_time: string; // "2026-08-31T11:13"
}

export interface LiveAccount {
  asset: number;
  positions: LivePosition[];
  channel_used?: string;
}

export interface LiveTradeLog {
  ts: string;
  mode: string; // "execute" | "quote" | ...
  code: string;
  name?: string; // 富途订单自带 stock_name；cn 由前端 stockNames 解析
  side?: string; // BUY/SELL（富途）；cn live_llm_trade 仅买入
  volume: number;
  price?: number | null; // 桥 filled_price（成交价）
  limit_price?: number | null;
  result?: { order_id?: string; status?: string; message?: string } | null;
  message?: string | null;
}

export const fetchLiveAccount = () => unwrap<LiveAccount>(api.get('/live/account'));
export const fetchLiveTrades = () => unwrap<LiveTradeLog[]>(api.get('/live/trades'));

// ---------- 港股富途实盘账户（BayMax backend /api/futu/* 直连 OpenD 网关） ----------
// 富途模拟/实盘账户；futu 原始 positions 是 {code: {...}} dict，reshape 成 cn LiveAccount
// 同一 shape，Live 页持仓/实盘 tab 复用 cn 渲染逻辑（排版与 A 股一致）。
interface FutuPositionRaw {
  volume: number;
  available_volume: number;
  price: number;
  market_value: number;
  cost: number;
  name: string;
  currency: string;
}
interface FutuAccountRaw {
  total_asset: number;
  cash: number;
  market_value: number;
  positions: Record<string, FutuPositionRaw>;
}

export const fetchFutuAccount = async (env = 'SIMULATE'): Promise<LiveAccount> => {
  const res = await api.get('/futu/account', { params: { env } });
  // 后端统一信封 {success, data:{...}}；account 在 data.data
  const raw = (res.data?.data ?? res.data) as FutuAccountRaw | undefined;
  if (!raw) return { asset: 0, positions: [], channel_used: `futu-${env.toLowerCase()}` };
  const positions: LivePosition[] = Object.entries(raw.positions ?? {})
    .filter(([, p]) => Number(p.volume) > 0)
    .map(([code, p]) => {
      const cost = Number(p.cost) || 0;
      const last = Number(p.price) || 0;
      const vol = Number(p.volume) || 0;
      const posVal = Number(p.market_value) || last * vol;
      return {
        stock_code: code,
        name: p.name || code,
        cost_price: cost,
        total_volume: vol,
        available_volume: Number(p.available_volume) || 0,
        last_price: last,
        position_value: posVal,
        pnl_pct: cost && last ? +(((last - cost) / cost) * 100).toFixed(2) : 0,
        pnl: cost && last ? +((last - cost) * vol).toFixed(2) : 0,
        buy_time: '',
      };
    });
  return {
    asset: Number(raw.total_asset) || 0,
    positions,
    channel_used: `futu-${env.toLowerCase()}`,
  };
};

/** 一次子进程拉 REAL+SIMULATE 两套账户（省一次 RSA 握手，降实盘 tab 延迟）。
 *  供 Live.tsx 挂在 15s 后台轮询，让实盘 tab 点击即见（数据始终 warm）。 */
export const fetchFutuAccountBoth = async (): Promise<{
  real: LiveAccount;
  simulate: LiveAccount;
}> => {
  const res = await api.get('/futu/account-both');
  const raw = (res.data?.data ?? res.data) as
    | { real?: FutuAccountRaw; simulate?: FutuAccountRaw }
    | undefined;
  const reshape = (r: FutuAccountRaw | undefined, env: string): LiveAccount => {
    if (!r) return { asset: 0, positions: [], channel_used: `futu-${env}` };
    const positions: LivePosition[] = Object.entries(r.positions ?? {})
      .filter(([, p]) => Number(p.volume) > 0)
      .map(([code, p]) => {
        const cost = Number(p.cost) || 0;
        const last = Number(p.price) || 0;
        const vol = Number(p.volume) || 0;
        const posVal = Number(p.market_value) || last * vol;
        return {
          stock_code: code,
          name: p.name || code,
          cost_price: cost,
          total_volume: vol,
          available_volume: Number(p.available_volume) || 0,
          last_price: last,
          position_value: posVal,
          pnl_pct: cost && last ? +(((last - cost) / cost) * 100).toFixed(2) : 0,
          pnl: cost && last ? +((last - cost) * vol).toFixed(2) : 0,
          buy_time: '',
        };
      });
    return {
      asset: Number(r.total_asset) || 0,
      positions,
      channel_used: `futu-${env}`,
    };
  };
  return {
    real: reshape(raw?.real, 'real'),
    simulate: reshape(raw?.simulate, 'simulate'),
  };
};

// 市场感知实盘账户：cn 走通达信桥 /live/account；hk 走富途；us 无实盘返回空
export const fetchLiveAccountFor = (market: MarketId): Promise<LiveAccount> =>
  market === 'hk' ? fetchFutuAccount('SIMULATE') : fetchLiveAccount();

// ---------- 港股富途订单历史（BayMax backend /api/futu/orders 直连 OpenD） ----------
// order_list_query → LiveTradeLog（同 shape，复用 cn 成交渲染）。只取 dealt_qty>0 已成交。
interface FutuOrderRaw {
  order_id: string;
  code: string;
  name: string;
  trd_side: string;
  order_type: string;
  order_status: string;
  qty: number;
  price: number;
  dealt_qty: number;
  dealt_avg_price: number;
  create_time: string;
  last_err_msg: string;
}

export const fetchFutuTrades = async (env = 'SIMULATE'): Promise<LiveTradeLog[]> => {
  const res = await api.get('/futu/orders', { params: { env } });
  const data = (res.data?.data ?? res.data) as { orders: FutuOrderRaw[] } | undefined;
  const orders = data?.orders ?? [];
  return orders
    .filter((o) => Number(o.dealt_qty) > 0) // CANCELLED/未成交不计入成交流
    .map((o) => ({
      ts: String(o.create_time || '').replace(' ', 'T'),
      mode: 'execute',
      code: o.code,
      name: o.name,
      side: o.trd_side,
      volume: Number(o.dealt_qty) || 0,
      price: Number(o.dealt_avg_price) || null,
      limit_price: Number(o.price) || null,
      result: { order_id: o.order_id, status: o.order_status, message: o.last_err_msg || '' },
      message: o.last_err_msg || '',
    }))
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
};

// 市场感知成交：cn 走通达信 live_trade 日志 /live/trades；hk 走富途订单历史
export const fetchLiveTradesFor = (market: MarketId): Promise<LiveTradeLog[]> =>
  market === 'hk' ? fetchFutuTrades('SIMULATE') : fetchLiveTrades();

// ---------- 港股富途已平仓（BayMax backend /api/futu/closed 直连 OpenD） ----------
// position_list_query 已平仓行（qty==0, realized_pl!=0）→ Live「已完成」tab 港股面板。
export interface FutuClosedRow {
  code: string;
  name: string;
  cost_price: number; // 入场成本
  last_price: number; // 平仓参考价（nominal_price）
  realized_pl: number; // 实现盈亏
  currency: string;
}

export const fetchFutuClosed = async (env = 'SIMULATE'): Promise<FutuClosedRow[]> => {
  const res = await api.get('/futu/closed', { params: { env } });
  const data = (res.data?.data ?? res.data) as { closed?: FutuClosedRow[] } | undefined;
  return data?.closed ?? [];
};

// ---------- 实盘分账（每 agent ¥10 万虚拟子账户） ----------

export interface LedgerPosition {
  code: string;
  name: string;
  volume: number;
  cost_price: number;
  position_value: number;
  buy_ts: string;
}

export interface AgentLedger {
  quota: number;
  used: number;
  remaining: number;
  positions: LedgerPosition[];
}

export const fetchLiveLedger = () =>
  unwrap<{ agents: Record<string, AgentLedger> }>(api.get('/live/ledger'));

// ---------- 盘中新闻（/live/news → quantmind /api/v1/news/articles，Huntly/RSS 聚合 + enrichment） ----------

export interface NewsEnrichment {
  tickers?: string[];
  industries?: string[];
  sentiment_label?: 'bullish' | 'bearish' | 'neutral' | null;
  sentiment_score?: number | null;
}

export interface NewsArticle {
  id: number;
  title: string;
  summary?: string | null;
  url?: string | null;
  source_name?: string | null;
  published_at?: string | null;
  enrichment?: NewsEnrichment | null;
}

export interface LiveNews {
  articles: NewsArticle[];
  error?: string | null;
}

/** 盘中实时新闻（tickers 逗号分隔；keyword 全文关键词；hours=回溯小时；按时间倒序）。
 *  A 股按代码 tickers 筛；港股无 HK 标签库，传 keyword（腾讯/恒生/港股）做标题全文搜。 */
export const fetchLiveNews = (
  tickers: string[],
  hours = 12,
  limit = 30,
  keyword = '',
) =>
  unwrap<LiveNews>(
    api.get('/live/news', {
      params: {
        tickers: tickers.join(','),
        hours,
        limit,
        ...(keyword ? { keyword } : {}),
      },
    }),
  );

// ---------- 实盘账户净值（总账户净值图） ----------

export interface LiveEquityPoint {
  date: string;
  ts: string;
  value: number;
}

export interface LiveEquity {
  /** 总账户（桥实时总资产） */
  total: LiveEquityPoint[];
  /** 每 agent 分账虚拟净值（¥10 万起：虚拟现金 + 名下持仓 × 实时价） */
  agents: Record<string, LiveEquityPoint[]>;
}

export const fetchLiveEquity = () => unwrap<LiveEquity>(api.get('/live/equity'));

// ---------- 实盘 LLM 分析 token 累计（/api/token-usage） ----------

export interface AgentTokenUsage {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  /** 估算条数（回填数据无真实 usage） */
  estimated: number;
  last_ts?: string | null;
}

export const fetchTokenUsage = () =>
  unwrap<{ agents: Record<string, AgentTokenUsage> }>(api.get('/token-usage'));

// ---------- 平仓明细（LAST 25 TRADES） ----------

export interface ClosedTradeDetail {
  symbol: string;
  exit_date: string; // YYYY-MM-DD
  qty: number;
  entry_price: number;
  exit_price: number;
  notional: number;
  fee: number;
  pnl: number;
  hold_days: number | null;
}

/** FIFO 重建已平仓逐笔，最新在前（最多 limit 笔） */
export const fetchTradeDetail = (agent: string, market: MarketId, limit = 25) =>
  unwrap<ClosedTradeDetail[]>(
    api.get(`/agents/${encodeURIComponent(agent)}/trade-detail`, { params: { market, limit } }),
  );

// ---------- 持仓明细（数量/成本/市值/盈亏） ----------

export interface HoldingRow {
  symbol: string;
  qty: number;
  entry_price: number;
  price: number;
  market_value: number;
  pnl: number;
  pnl_pct: number | null;
  change_pct: number | null;
  weight_pct: number | null;
}

export interface Holdings {
  holdings: HoldingRow[];
  cash: number;
  total_market_value: number;
  total_equity: number;
}

export const fetchHoldings = (agent: string, market: MarketId) =>
  unwrap<Holdings>(api.get(`/agents/${encodeURIComponent(agent)}/holdings`, { params: { market } }));

// ---------- 基准（指数） ----------

export interface BenchPoint {
  time: string; // YYYY-MM-DD
  close: number;
}

/** 基准文件统一为 AlphaVantage 风格 {"Meta Data", "Time Series (Daily)"|"(60min)": {ts: {"4. close"}}}。
 *  US 用脚本生成的等权 NASDAQ-100（data/benchmark_nasdaq100.json，与 agent 数据同步）；
 *  CN 用 SSE50 指数；HK 暂无指数文件。
 */
const parseBenchFile = (doc: unknown): BenchPoint[] => {
  const series = (doc as { 'Time Series (Daily)'?: Record<string, Record<string, string>> })[
    'Time Series (Daily)'
  ];
  if (!series) return [];
  return Object.entries(series)
    .map(([time, bar]) => ({ time, close: Number(bar['4. close']) }))
    .filter((p) => Number.isFinite(p.close));
};

// ---------- 比赛配置（每模型多选分析配置） ----------

export interface CompMode {
  id: string;
  name: string;
  prompt: string;
}

export type CompSelection = Record<string, string[]>;

export const fetchCompConfig = (market: MarketId = 'cn') =>
  unwrap<{ market: string; catalog: CompMode[]; selection: CompSelection }>(
    api.get('/comp-config', { params: { market } }),
  );

export const saveCompConfig = (market: MarketId, selection: CompSelection) =>
  unwrap<{ market: string; selection: CompSelection }>(
    api.put('/comp-config', { selection }, { params: { market } }),
  );

// ---------- 实盘同步数据（quantmind PG，通达信实盘账户） ----------

export interface RealAccountPosition {
  symbol: string;
  name: string;
  volume: number;
  cost_price: number;
  price: number;
  market_value: number;
  available_volume: number;
}

export interface RealAccount {
  ts: string | null;
  total_asset: number;
  cash: number;
  market_value: number;
  today_pnl: number;
  total_pnl: number;
  positions: RealAccountPosition[];
}

export interface RealLedgerRow {
  date: string;
  total_asset: number;
  cash: number;
  market_value: number;
  daily_return_pct: number | null;
  total_return_pct: number | null;
  position_count: number | null;
  source: string;
}

export interface L2FactorRow {
  ts: string;
  symbol: string;
  stock_code: string;
  name: string | null;
  now_price: number | null;
  factors: Record<string, number | null>;
}

export const fetchRealAccount = () => unwrap<RealAccount>(api.get('/live/real-account'));
export const fetchRealLedger = () => unwrap<RealLedgerRow[]>(api.get('/live/real-ledger'));
export const fetchL2Factors = (limit = 200) =>
  unwrap<L2FactorRow[]>(api.get('/live/l2-factors', { params: { limit } }));

// ---------- 当日实时指数（顶部行情条） ----------

export interface IndexQuote {
  code: string;
  name: string;
  last: number; // 点位/最新价
  change_pct: number; // 百分数（0.86 = +0.86%），与实盘接口口径一致
}

/** CN：通达信桥日K聚合 6 个主流指数（盘中实时）；US：NDX100 基准文件；HK 空。 */
export const fetchIndices = (market: MarketId) =>
  unwrap<{ indices: IndexQuote[] }>(api.get('/live/indices', { params: { market } }));

export const fetchBenchmark = async (market: MarketId): Promise<BenchPoint[]> => {
  try {
    const file =
      market === 'us'
        ? '/data/benchmark_nasdaq100.json'
        : market === 'cn'
          ? '/data/A_stock/index_daily_sse_50.json'
          : null;
    if (!file) return [];
    const res = await api.get(file);
    return parseBenchFile(res.data);
  } catch {
    return [];
  }
};
