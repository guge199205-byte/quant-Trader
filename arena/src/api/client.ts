/** Quant Agent Trader 数据层：对接 FastAPI 8091 现有端点。
 *  生产环境经 nginx 同源反代（token 由 nginx 注入），浏览器无需持有 token；
 *  dev 模式直连 vite proxy 到 127.0.0.1:8091（api 鉴权未配置时直接可用）。
 */
import axios from 'axios';

export const api = axios.create({ baseURL: '/api', timeout: 20000 });

// ---------- 后端数据结构（与 api_server.py / agent_data.py 对齐） ----------

export type MarketId = 'us' | 'cn' | 'hk';

export const MARKETS: { id: MarketId; label: string; name: string; currency: string }[] = [
  { id: 'us', label: 'US', name: '美股 · NASDAQ 100', currency: '$' },
  { id: 'cn', label: 'CN', name: 'A股 · SSE 50', currency: '¥' },
  { id: 'hk', label: 'HK', name: '港股 · 恒指成分', currency: 'HK$' },
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
  new_messages?: { role?: string; content?: string }[];
}

/** /agents/{name}/trades 返回：顶层 action/symbol/amount/cash_after */
export interface TradeRecord {
  date: string;
  action: string; // 'buy' | 'sell'
  symbol: string;
  amount: number;
  cash_after: number;
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
