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
  volume: number;
  price?: number | null; // 桥 filled_price（成交价）
  limit_price?: number | null;
  result?: { order_id?: string; status?: string; message?: string } | null;
  message?: string | null;
}

export const fetchLiveAccount = () => unwrap<LiveAccount>(api.get('/live/account'));
export const fetchLiveTrades = () => unwrap<LiveTradeLog[]>(api.get('/live/trades'));

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

export const fetchCompConfig = () =>
  unwrap<{ catalog: CompMode[]; selection: CompSelection }>(api.get('/comp-config'));

export const saveCompConfig = (selection: CompSelection) =>
  unwrap<{ selection: CompSelection }>(api.put('/comp-config', { selection }));

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
