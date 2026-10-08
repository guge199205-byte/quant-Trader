/** 个股终端 API 服务（本地独立版：已加内存缓存，重复查看同一股票/图表秒开） */

import axios, { AxiosInstance } from 'axios';
import { SERVICE_ENDPOINTS } from '../../../config/services';
import { authService } from '../../auth/services/authService';
import { KlineBar, StockListResponse, StockProfile } from '../types';

// —— 轻量内存缓存：稳定数据（K线/概况/财务/标签等）重复请求直接命中 ——
// 键 = 方法名 + 参数；TTL 过期自动失效；只缓存有数据的结果（失败/空的回退值不缓存）
const _memo = new Map<string, { at: number; value: unknown; ttl: number }>();

function _memoGet<T>(key: string): T | undefined {
  const hit = _memo.get(key);
  if (!hit) return undefined;
  if (Date.now() - hit.at > hit.ttl) {
    _memo.delete(key);
    return undefined;
  }
  return hit.value as T;
}

function _memoSet(key: string, value: unknown, ttlMs: number): void {
  _memo.set(key, { at: Date.now(), value, ttl: ttlMs });
  if (_memo.size > 300) {
    const stale = [..._memo.entries()].filter(([, e]) => Date.now() - e.at > e.ttl);
    stale.forEach(([k]) => _memo.delete(k));
  }
  if (_memo.size > 300) {
    const oldest = [..._memo.entries()].sort((a, b) => a[1].at - b[1].at).slice(0, 100);
    oldest.forEach(([k]) => _memo.delete(k));
  }
}

const MIN10 = 10 * 60_000;
const MIN5 = 5 * 60_000;
const HOUR = 60 * 60_000;

class StockTerminalService {
  private get client(): AxiosInstance {
    const baseURL = (import.meta as any).env?.VITE_USER_API_URL || SERVICE_ENDPOINTS.API_GATEWAY || SERVICE_ENDPOINTS.USER_SERVICE;
    const client = axios.create({ baseURL, timeout: 30000 });
    client.interceptors.request.use((config) => {
      const token = authService.getAccessToken();
      if (token) {
        if (config.headers && typeof config.headers.set === 'function') {
          config.headers.set('Authorization', `Bearer ${token}`);
        } else if (config.headers) {
          config.headers['Authorization'] = `Bearer ${token}`;
        }
      }
      return config;
    });
    return client;
  }

  async getStockList(params: {
    market?: string;
    industry?: string;
    concept?: string;
    q?: string;
    date?: string;
    score_min?: number;
    score_max?: number;
    model?: string;
    board?: string;
    cap_tier?: string;
    trend?: string;
    tag?: string;
    index_code?: string;
    side?: string;
    /** 排除 ST 股 */
    exclude_st?: boolean;
    page?: number;
    page_size?: number;
    /** 附带各筛选下拉选项的命中数（option_counts） */
    with_counts?: boolean;
    /** 定位股票（600519.SH），返回当前排序中的名次（find_rank）供列表跳转 */
    find_symbol?: string;
    /** 自选股列表（逗号分隔，prefix/suffix/纯代码均可），按当前排序保留分数降序 */
    symbols?: string;
  }): Promise<StockListResponse> {
    const key = `list:${JSON.stringify(params)}`;
    const hit = _memoGet<StockListResponse>(key);
    if (hit) return hit;
    const resp = await this.client.get('/stock-terminal/list', { params });
    const data = resp.data?.data ?? { total: 0, page: 1, page_size: 100, trade_date: '', items: [] };
    if (data.items?.length) _memoSet(key, data, 20_000); // 列表 20s 新鲜度
    return data;
  }

  async getConcepts(): Promise<string[]> {
    const hit = _memoGet<string[]>(`concepts`);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/concepts');
      const concepts = resp.data?.data?.concepts ?? [];
      if (concepts.length) _memoSet('concepts', concepts, HOUR);
      return concepts;
    } catch {
      return [];
    }
  }

  async getIndustries(): Promise<string[]> {
    const hit = _memoGet<string[]>(`industries`);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/industries');
      const industries = resp.data?.data?.industries ?? [];
      if (industries.length) _memoSet('industries', industries, HOUR);
      return industries;
    } catch {
      return [];
    }
  }

  async getProfile(symbol: string, date?: string): Promise<StockProfile | null> {
    const key = `profile:${symbol}:${date ?? ''}`;
    const hit = _memoGet<StockProfile | null>(key);
    if (hit !== undefined) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/profile', { params: { symbol, ...(date ? { date } : {}) } });
      const profile = resp.data?.data ?? null;
      if (profile) _memoSet(key, profile, MIN10);
      return profile;
    } catch {
      return null;
    }
  }

  async getDailyKline(symbol: string, days = 500): Promise<KlineBar[]> {
    const key = `kline:${symbol}:${days}`;
    const hit = _memoGet<KlineBar[]>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/market/kline', {
        params: { symbol, market: 'A', days },
      });
      const items = resp.data?.data?.items ?? [];
      const bars = items.map((it: any) => ({
        date: String(it.date ?? '').slice(0, 10),
        open: Number(it.open),
        high: Number(it.high),
        low: Number(it.low),
        close: Number(it.close),
        volume: it.volume != null ? Number(it.volume) : null,
        amount: it.amount != null ? Number(it.amount) : null,
      })).filter((b: KlineBar) => b.date && Number.isFinite(b.close));
      if (bars.length) _memoSet(key, bars, MIN10);
      return bars;
    } catch {
      return [];
    }
  }

  async getIndexKline(symbol: string, days = 500): Promise<{ date: string; close: number }[]> {
    const key = `indexKline:${symbol}:${days}`;
    const hit = _memoGet<{ date: string; close: number }[]>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/market/index-kline', {
        params: { symbol, days },
      });
      const data = resp.data?.data ?? {};
      const dates: string[] = data.dates ?? [];
      const closes: number[] = data.close ?? [];
      const rows = dates.map((d, i) => ({ date: String(d).slice(0, 10), close: Number(closes[i]) }))
        .filter(x => x.date && Number.isFinite(x.close));
      if (rows.length) _memoSet(key, rows, MIN10);
      return rows;
    } catch {
      return [];
    }
  }

  /** 指数快照（上证/深成/沪深300/中证500/创业板/科创50/上证50/北证50）；asof 取历史日及之前最近行情 */
  async getIndexQuotes(asof?: string): Promise<IndexQuote[]> {
    const key = `quotes:${asof ?? ''}`;
    const hit = _memoGet<IndexQuote[]>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/market/quotes', { params: { market: 'CN', ...(asof ? { asof } : {}) } });
      const quotes = resp.data?.data?.quotes ?? [];
      if (quotes.length) _memoSet(key, quotes, asof ? MIN10 : 30_000);
      return quotes;
    } catch {
      return [];
    }
  }

  /** 大盘均线过滤（上证指数 MA5/10/20/30/60 + 可持仓判断） */
  async getIndexMa(asof?: string): Promise<IndexMa | null> {
    const key = `indexMa:${asof ?? ''}`;
    const hit = _memoGet<IndexMa | null>(key);
    if (hit !== undefined) return hit;
    try {
      const resp = await this.client.get('/market/index-ma', {
        params: { symbol: '000001.SH', ...(asof ? { asof } : {}) },
      });
      const data = resp.data?.data ?? null;
      if (data) _memoSet(key, data, asof ? MIN10 : 60_000);
      return data;
    } catch {
      return null;
    }
  }

  /** 大盘 MA20 日历（上证收盘/MA20/偏离度 + 当日推理概况），供日期筛选弹层着色 */
  async getMarketCalendar(months = 12, model?: string, refresh = false): Promise<MarketCalendarData> {
    const key = `calendar:${months}:${model ?? ''}`;
    const hit = _memoGet<MarketCalendarData>(key);
    if (hit && !refresh) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/market-calendar', {
        params: { months, ...(model ? { model } : {}), ...(refresh ? { refresh: true } : {}) },
      });
      const data = resp.data?.data ?? { index_symbol: '000001.SH', index_name: '上证指数', days: [] };
      if (data.days?.length) _memoSet(key, data, 30 * 60_000);
      return data;
    } catch {
      return { index_symbol: '000001.SH', index_name: '上证指数', days: [] };
    }
  }

  async getMinuteKline(symbol: string, freq: 'min5' | 'min1', days = 10): Promise<{ items: KlineBar[]; available: boolean }> {
    const key = `minute:${symbol}:${freq}:${days}`;
    const hit = _memoGet<{ items: KlineBar[]; available: boolean }>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/minute', { params: { symbol, freq, days } });
      const data = resp.data?.data ?? {};
      const items = (data.items ?? []).map((it: any) => ({
        date: String(it.date ?? ''),
        open: Number(it.open),
        high: Number(it.high),
        low: Number(it.low),
        close: Number(it.close),
        volume: it.volume != null ? Number(it.volume) : null,
        amount: it.amount != null ? Number(it.amount) : null,
      }));
      const result = { items, available: !!data.available };
      if (items.length) _memoSet(key, result, MIN5);
      return result;
    } catch {
      return { items: [], available: false };
    }
  }

  async getFinancials(symbol: string, limit = 8, date?: string): Promise<FinancialsResponse> {
    const key = `financials:${symbol}:${limit}:${date ?? ''}`;
    const hit = _memoGet<FinancialsResponse>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/financials', { params: { symbol, limit, ...(date ? { date } : {}) } });
      const data = resp.data?.data ?? { symbol, periods: [], income: [], balance: [], cashflow: [], per_share: [] };
      if (data.periods?.length) _memoSet(key, data, MIN10);
      return data;
    } catch {
      return { symbol, periods: [], income: [], balance: [], cashflow: [], per_share: [] };
    }
  }

  async getSeries(symbol: string, group: string, years = 3, endDate?: string): Promise<SeriesResponse> {
    const key = `series:${symbol}:${group}:${years}:${endDate ?? ''}`;
    const hit = _memoGet<SeriesResponse>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/series', { params: { symbol, group, years, ...(endDate ? { end_date: endDate } : {}) } });
      const data = resp.data?.data ?? { dates: [], columns: {} };
      if (data.dates?.length) _memoSet(key, data, MIN10);
      return data;
    } catch {
      return { dates: [], columns: {} };
    }
  }

  async getNews(symbol: string): Promise<{ items: any[]; available: boolean }> {
    const key = `news:${symbol}`;
    const hit = _memoGet<{ items: any[]; available: boolean }>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/news', { params: { symbol } });
      const data = resp.data?.data ?? { items: [], available: false };
      if (data.items?.length) _memoSet(key, data, MIN10);
      return data;
    } catch {
      return { items: [], available: false };
    }
  }

  async getAiBacktest(symbol: string, hint = ''): Promise<any> {
    const resp = await this.client.get('/stock-terminal/ai-backtest', { params: { symbol, hint }, timeout: 60000 });
    return resp.data?.data;
  }

  async getChartBacktest(symbol: string, buyExpr: string, sellExpr: string, days = 500): Promise<any> {
    const resp = await this.client.get('/stock-terminal/chart-backtest', {
      params: { symbol, buy_expr: buyExpr, sell_expr: sellExpr, days },
      timeout: 60000,
    });
    return resp.data?.data;
  }

  async getSignalOverlay(symbol: string, days = 250): Promise<Record<string, { date: string; fusion: number | null; side: string }[]>> {
    const key = `overlay:${symbol}:${days}`;
    const hit = _memoGet<Record<string, { date: string; fusion: number | null; side: string }[]>>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/signal-overlay', { params: { symbol, days } });
      const series = resp.data?.data?.series ?? {};
      if (Object.keys(series).length) _memoSet(key, series, 30 * 60_000);
      return series;
    } catch {
      return {};
    }
  }

  async getTags(symbol: string): Promise<{ tags: any[]; presets: any[] }> {
    const key = `tags:${symbol}`;
    const hit = _memoGet<{ tags: any[]; presets: any[] }>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/tags', { params: { symbol }, timeout: 30000 });
      const data = resp.data?.data ?? { tags: [], presets: [] };
      if (data.tags?.length) _memoSet(key, data, MIN10);
      return data;
    } catch {
      return { tags: [], presets: [] };
    }
  }

  /** 标签同类股票：返回 {items, score_min, score_max}（当前模型全市场分数极值，供动态归一化显示） */
  async getTagStocks(tagId: string, limit = 30): Promise<{ items: any[]; score_min: number | null; score_max: number | null }> {
    const key = `tagStocks:${tagId}:${limit}`;
    const hit = _memoGet<{ items: any[]; score_min: number | null; score_max: number | null }>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get(`/stock-terminal/tags/${tagId}/stocks`, { params: { limit }, timeout: 30000 });
      const data = resp.data?.data ?? {};
      const result = { items: data.items ?? [], score_min: data.score_min ?? null, score_max: data.score_max ?? null };
      if (result.items.length) _memoSet(key, result, MIN10);
      return result;
    } catch {
      return { items: [], score_min: null, score_max: null };
    }
  }

  async getDividends(symbol: string, date?: string): Promise<DividendItem[]> {
    const key = `dividends:${symbol}:${date ?? ''}`;
    const hit = _memoGet<DividendItem[]>(key);
    if (hit) return hit;
    try {
      const resp = await this.client.get('/stock-terminal/dividends', { params: { symbol, ...(date ? { date } : {}) } });
      const items = resp.data?.data?.items ?? [];
      if (items.length) _memoSet(key, items, MIN10);
      return items;
    } catch {
      return [];
    }
  }
}

export interface FinRecord { period: string; items: Record<string, number | null>; }
export interface IndexQuote {
  symbol: string;
  name: string;
  price: number;
  change: number;
  change_percent: number;
  trade_date?: string;
}
export interface IndexMa {
  symbol: string;
  name: string;
  trade_date: string;
  close: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma30: number | null;
  ma60: number | null;
  above_ma20: boolean;
  status: string;
}
export interface MarketCalendarDay {
  date: string;          // YYYY-MM-DD
  close: number;
  ma20: number;
  dev_pct: number;       // (close-ma20)/ma20*100，正=高于均线
  signal_count?: number; // 当日有分数的信号行数
  top10_avg?: number | null;  // 当日 Top10 推理信号平均分
  has_inference?: boolean;
}
export interface MarketCalendarData {
  index_symbol: string;
  index_name: string;
  days: MarketCalendarDay[];
}
export interface FinancialsResponse {
  symbol: string;
  periods: string[];
  income: FinRecord[];
  balance: FinRecord[];
  cashflow: FinRecord[];
  per_share: FinRecord[];
}
export interface SeriesResponse { dates: string[]; columns: Record<string, (number | null)[]>; }
export interface DividendItem {
  date: string; interest: number | null; stock_bonus: number | null;
  stock_gift: number | null; gugai: number | null; dr: number | null;
}

export const stockTerminalService = new StockTerminalService();