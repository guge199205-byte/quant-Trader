/** 行情回测页共用的格式化与错误文案。
 *
 * 数值/金额直接复用全站 `utils/format`，这里只包一层默认值，避免各写一套。
 * ⚠️ `fmtPct` 的入参是**百分数**（TradingView 口径，如 62.35 表示 62.35%），
 *    与 `utils/format` 的 `fmtPct`（入参是小数 0.6235）不同——所以这里必须转一道。
 */
import { fmtMoney as moneyText, fmtNum, fmtPct as ratioPct } from '../../utils/format';

export const fmt = fmtNum;

export const fmtPct = (v: number | null | undefined, digits = 2): string =>
  ratioPct(v == null ? v : v / 100, digits);

export const fmtMoney = (v: number | null | undefined, unit = '¥'): string =>
  moneyText(v, unit, 0);

/** axios 错误 → 人话（后端 400 的 detail 优先） */
export const errText = (e: unknown): string => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (e instanceof Error ? e.message : String(e));
};

/** 语料来源：edited > recrawl > desktop */
export const KIND_LABEL: Record<string, string> = {
  desktop: '原始语料',
  recrawl: '重爬（缩进完整）',
  edited: '已编辑',
  missing: '缺失',
};

/** Pine 转写任务的阶段文案 */
export const STAGE_LABEL: Record<string, string> = {
  queued: '已入队，等宿主 worker 取（每分钟一轮）',
  transpile: '转写中：调模型翻成 Pyne-Python…',
  static: '静态检查未通过',
  backtest: '沙箱回测中（bwrap：只读根 + 断网）…',
  repair: '回测报错，让模型修一版再重跑…',
  done: '完成',
  worker: 'worker 异常',
};
