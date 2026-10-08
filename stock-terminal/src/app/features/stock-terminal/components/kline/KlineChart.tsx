/** 个股终端 K 线图：主图（蜡烛+MA/BOLL+指数叠加+交易/参考线）+ 副图（VOL/MACD/KDJ/RSI）+ 推理分数副图（多模型+策略提醒+参考线） */

import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { KlineBar } from '../../types';
import { boll, kdj, macd, rsi, sma, volMa, Series } from '../../engine/indicators';

export type SubplotType = 'vol' | 'macd' | 'kdj' | 'rsi';

export interface IndicatorConfig {
  ma: boolean;
  boll: boolean;
  subplots: SubplotType[];
}

export interface IndexOverlay {
  code: string;
  name: string;
  closes: { date: string; close: number }[];
  color: string;
}

export interface SignalPoint {
  date: string;
  fusion: number | null;
  side: string;
}

/** 推理分数历史叠加（多模型）：每模型一条分数线 */
export interface ScoreSeries {
  model: string;
  color: string;
  points: { date: string; fusion: number | null; side: string | null }[];
}

/** 策略提醒点：标记在分数副图上 */
export interface AlertPoint {
  date: string;
  severity: 'danger' | 'warning' | 'positive' | 'info';
  message: string;
  score?: number | null;
}

/** 模拟交易点：buy/sell */
export interface TradeMarker {
  date: string;
  side: 'buy' | 'sell';
  price: number;
  shares: number;
}

/** 参考线：分数轴虚线 */
export interface RefLine {
  id: string;
  value: number;
  label: string;
  color: string;
  visible?: boolean;
}

const COLORS = {
  up: '#e11d48',        // A股：涨红
  down: '#059669',      // 跌绿
  ma5: '#f59e0b',
  ma10: '#3b82f6',
  ma20: '#8b5cf6',
  ma60: '#64748b',
  boll: '#94a3b8',
  volUp: '#fda4af',
  volDown: '#6ee7b7',
  dif: '#3b82f6',
  dea: '#f59e0b',
  histUp: '#e11d48',
  histDown: '#059669',
  k: '#3b82f6',
  d: '#f59e0b',
  j: '#8b5cf6',
  rsi: '#6366f1',
};

const SEVERITY_COLOR: Record<AlertPoint['severity'], string> = {
  danger: '#e11d48',
  warning: '#f59e0b',
  positive: '#10b981',
  info: '#6366f1',
};

const AXIS_LABEL = { fontSize: 10, color: '#64748b' };
const AXIS_LINE = { lineStyle: { color: '#e2e8f0' } };
const SPLIT_LINE = { lineStyle: { color: '#f1f5f9' } };
const SUB_HEIGHT = 84;  // 每个副图高度 px
/** 默认黄金线（策略 v2.0 主板黄金买入区间 0.10-0.12 的下沿） */
const DEFAULT_REF_LINE: RefLine = { id: 'default-golden', value: 0.10, label: '黄金线', color: '#10b981' };

interface Props {
  bars: KlineBar[];
  config: IndicatorConfig;
  overlays: IndexOverlay[];
  height?: number;
  signals?: SignalPoint[];
  btEquity?: { date: string; equity: number }[];
  scoreSeries?: ScoreSeries[];
  alerts?: AlertPoint[];
  trades?: TradeMarker[];
  refLines?: RefLine[];
  onBarClick?: (bar: KlineBar) => void;
}

export function KlineChart({
  bars, config, overlays, height = 460,
  signals = [], btEquity = [], scoreSeries = [], alerts = [], trades = [], refLines = [], onBarClick,
}: Props) {
  const option = useMemo(() => {
    const dates = bars.map(b => b.date);
    const closes = bars.map(b => b.close);
    const volumes = bars.map(b => b.volume ?? 0);
    const idxByDate = new Map(dates.map((d, i) => [d, i]));
    const ma5 = config.ma ? sma(closes, 5) : null;
    const ma10 = config.ma ? sma(closes, 10) : null;
    const ma20 = config.ma ? sma(closes, 20) : null;
    const ma60 = config.ma ? sma(closes, 60) : null;
    const bb = config.boll ? boll(closes) : null;
    const macdRes = config.subplots.includes('macd') ? macd(closes) : null;
    const kdjRes = config.subplots.includes('kdj') ? kdj(bars) : null;
    const rsiRes = config.subplots.includes('rsi') ? rsi(closes) : null;
    const volMa5 = config.subplots.includes('vol') ? volMa(bars, 5) : null;
    const volMa10 = config.subplots.includes('vol') ? volMa(bars, 10) : null;

    // 指数叠加：以各自首日为基准归一化为百分比
    const overlaySeries = overlays.map(ov => {
      const byDate = new Map(ov.closes.map(c => [c.date, c.close]));
      const base = ov.closes.length ? ov.closes[0].close : 1;
      const aligned = bars.map(b => {
        const c = byDate.get(b.date);
        return c != null && base > 0 ? Number((((c - base) / base) * 100).toFixed(2)) : null;
      });
      return { name: ov.name, data: aligned, color: ov.color };
    });

    // ── grid 布局：主图（蜡烛+MA+指数+模型分数右轴）+ 副图依次下排 ──
    // axes 下标与 grid 下标独立：用 gridAxes 记录每个 grid 的 x/y 轴在 xAxis/yAxis 数组中的下标
    const GRID_L = 64, GRID_R = scoreSeries.length ? 46 : 16;  // 右侧留出分数轴刻度
    const GAP = 28;                    // 主图与第一个副图间距
    const SUB_GAP = 24;                // 副图之间间距
    const TOP = 24;                    // 顶部留出图例行
    const subCount = config.subplots.length;
    const subTotal = subCount > 0 ? (subCount * SUB_HEIGHT + (subCount - 1) * SUB_GAP) : 0;
    const mainH = Math.max(140, height - TOP - GAP - subTotal - 18);
    const grids: any[] = [];
    const xAxes: any[] = [];
    const yAxes: any[] = [];
    const series: any[] = [];
    const gridAxes: { x: number; y: number }[] = []; // gridIdx -> axes idx

    // 主图
    grids.push({ left: GRID_L, right: GRID_R, top: TOP, height: mainH });
    xAxes.push({ type: 'category', gridIndex: 0, data: dates, boundaryGap: true, axisLine: AXIS_LINE, axisTick: { show: false }, axisLabel: { show: false } });
    yAxes.push({ type: 'value', gridIndex: 0, scale: true, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: SPLIT_LINE });
    gridAxes[0] = { x: 0, y: 0 };
    // 指数归一化百分比轴（主图左侧内沿，仅当叠加指数时不遮挡分数轴）
    if (overlaySeries.length) {
      yAxes.push({
        type: 'value', gridIndex: 0, scale: true,
        axisLabel: { show: false }, axisLine: { show: false }, splitLine: { show: false },
        min: (v: any) => -Math.max(30, Math.ceil(Math.abs(v.min) / 10) * 10),
        max: (v: any) => Math.max(30, Math.ceil(Math.abs(v.max) / 10) * 10),
      });
    }
    // 模型分数轴（主图右外侧）：推理分数与参考线共用此轴
    let scoreYI = -1;
    if (scoreSeries.length) {
      scoreYI = yAxes.length;
      const allScores = scoreSeries.flatMap(sr => sr.points.map(p => p.fusion).filter((f): f is number => f != null));
      const lo = allScores.length ? Math.min(...allScores) : -1;
      const hi = allScores.length ? Math.max(...allScores) : 1;
      // 分数轴按当前模型分数跨度自适应：原 min pad 0.05 对新模型（0.001 量级）过大，
      // 把轴撑到 ±0.06 刻度显得很大——改为纯比例 padding；
      // 单点/全等分数（span=0）时按分数绝对值比例兜底（0.002 下限），不再用 0.05
      const span = hi - lo;
      const pad = span > 1e-9 ? span * 0.15 : Math.max(0.002, Math.abs(hi) * 0.3);
      // 刻度小数位随量级收紧：跨度过小时 toFixed(2) 会全部显示 0.00
      const digits = span < 0.01 ? 4 : span < 0.1 ? 3 : 2;
      yAxes.push({
        type: 'value', gridIndex: 0, position: 'right', scale: false,
        min: lo - pad, max: hi + pad,
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => v.toFixed(digits) },
        axisLine: { lineStyle: { color: '#6366f1' } },
        splitLine: { show: false },
        name: '分数', nameTextStyle: { fontSize: 9, color: '#6366f1' },
      });
    }

    // 蜡烛
    series.push({
      name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
      data: bars.map(b => [b.open, b.close, b.low, b.high]),
      itemStyle: { color: COLORS.up, color0: COLORS.down, borderColor: COLORS.up, borderColor0: COLORS.down },
    });

    const line = (name: string, data: Series, color: string, yAxisIdx = 0, width = 1.2) =>
      series.push({
        name, type: 'line', xAxisIndex: 0, yAxisIndex: yAxisIdx, data,
        symbol: 'none', lineStyle: { width, color }, itemStyle: { color }, emphasis: { disabled: true }, z: 3,
      });

    if (ma5) line('MA5', ma5, COLORS.ma5);
    if (ma10) line('MA10', ma10, COLORS.ma10);
    if (ma20) line('MA20', ma20, COLORS.ma20);
    if (ma60) line('MA60', ma60, COLORS.ma60);
    if (bb) {
      line('BOLL中轨', bb.mid, COLORS.boll);
      line('BOLL上轨', bb.upper, COLORS.boll);
      line('BOLL下轨', bb.lower, COLORS.boll);
    }
    overlaySeries.forEach((ov, i) => line(ov.name, ov.data, ov.color, 1, 1.2));

    // 策略净值叠加
    if (btEquity.length) {
      const eqByDate = new Map(btEquity.map(p => [p.date, p.equity]));
      const firstEq = btEquity.length ? btEquity[0].equity : 1;
      const baseClose = bars.length ? bars[0].close : 1;
      series.push({
        name: '策略净值', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        data: bars.map(b => {
          const eq = eqByDate.get(b.date);
          if (eq == null || firstEq <= 0) return null;
          return Number((baseClose * (eq / firstEq)).toFixed(2));
        }),
        symbol: 'none', lineStyle: { width: 1.6, color: '#f97316', type: 'dashed' }, itemStyle: { color: '#f97316' }, z: 4, emphasis: { disabled: true },
      });
    }

    // 推理信号标记
    if (signals.length) {
      const buyData: any[] = [], sellData: any[] = [];
      for (const sig of signals) {
        const i = idxByDate.get(sig.date);
        if (i == null) continue;
        const bar = bars[i];
        const v = sig.side === 'BUY' ? bar.low * 0.99 : bar.high * 1.01;
        if (sig.side === 'BUY') buyData.push({ value: [i, Number(v.toFixed(2))], sig });
        else if (sig.side === 'SELL') sellData.push({ value: [i, Number(v.toFixed(2))], sig });
      }
      const mk = (data: any[], symbol: string, color: string, offset: number) => ({
        name: '信号', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
        data, symbol, symbolSize: 11, symbolOffset: [0, offset],
        itemStyle: { color, borderColor: '#fff', borderWidth: 1 },
        label: { show: true, formatter: (p: any) => p.data.sig.side, fontSize: 8, color, fontWeight: 'bold', position: 'top' },
        z: 10,
      });
      if (buyData.length) series.push(mk(buyData, 'triangle', COLORS.up, -8));
      if (sellData.length) series.push(mk(sellData, 'triangle', COLORS.down, 8));
    }

    // 模拟交易标记
    if (trades.length) {
      const buyT: any[] = [], sellT: any[] = [];
      for (const t of trades) {
        const i = idxByDate.get(t.date);
        if (i == null) continue;
        const bar = bars[i];
        const v = t.side === 'buy' ? bar.low * 0.985 : bar.high * 1.015;
        if (t.side === 'buy') buyT.push({ value: [i, Number(v.toFixed(2))], t });
        else sellT.push({ value: [i, Number(v.toFixed(2))], t });
      }
      const tmk = (data: any[], symbol: string, color: string, offset: number) => ({
        name: '交易', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
        data, symbol, symbolSize: 13, symbolOffset: [0, offset],
        itemStyle: { color, borderColor: '#fff', borderWidth: 1.5 },
        label: { show: true, formatter: (p: any) => p.data.t.shares, fontSize: 8, color, fontWeight: 'bold', position: 'bottom' },
        z: 11,
      });
      if (buyT.length) series.push(tmk(buyT, 'triangle', COLORS.up, -12));
      if (sellT.length) series.push(tmk(sellT, 'triangle', COLORS.down, 12));
    }

    // ── 副图：依次下排（grids 1..N）──
    let subTop = TOP + mainH + GAP;
    config.subplots.forEach((sp, idx) => {
      const gi = idx + 1;
      const xi = xAxes.length;
      const yi = yAxes.length;
      grids.push({ left: GRID_L, right: GRID_R, top: subTop, height: SUB_HEIGHT });
      const showLabel = idx === config.subplots.length - 1;
      xAxes.push({
        type: 'category', gridIndex: gi, data: dates, boundaryGap: true,
        axisLine: AXIS_LINE, axisTick: { show: false },
        axisLabel: showLabel ? { ...AXIS_LABEL, color: '#94a3b8' } : { show: false },
      });
      yAxes.push({ type: 'value', gridIndex: gi, scale: true, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: SPLIT_LINE });
      gridAxes[gi] = { x: xi, y: yi };

      if (sp === 'vol') {
        series.push({
          name: '成交量', type: 'bar', xAxisIndex: xi, yAxisIndex: yi,
          data: volumes.map((v, i) => ({ value: v, itemStyle: { color: bars[i].close >= bars[i].open ? COLORS.volUp : COLORS.volDown } })),
        });
        series.push({ name: 'VMA5', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: volMa5, symbol: 'none', lineStyle: { width: 1, color: COLORS.ma5 }, z: 3 });
        series.push({ name: 'VMA10', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: volMa10, symbol: 'none', lineStyle: { width: 1, color: COLORS.ma10 }, z: 3 });
      } else if (sp === 'macd' && macdRes) {
        series.push({
          name: 'MACD柱', type: 'bar', xAxisIndex: xi, yAxisIndex: yi,
          data: macdRes.hist.map(v => ({ value: v, itemStyle: { color: (v ?? 0) >= 0 ? COLORS.histUp : COLORS.histDown } })),
        });
        series.push({ name: 'DIF', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: macdRes.dif, symbol: 'none', lineStyle: { width: 1, color: COLORS.dif }, z: 3 });
        series.push({ name: 'DEA', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: macdRes.dea, symbol: 'none', lineStyle: { width: 1, color: COLORS.dea }, z: 3 });
      } else if (sp === 'kdj' && kdjRes) {
        series.push({ name: 'K', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: kdjRes.k, symbol: 'none', lineStyle: { width: 1, color: COLORS.k }, z: 3 });
        series.push({ name: 'D', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: kdjRes.d, symbol: 'none', lineStyle: { width: 1, color: COLORS.d }, z: 3 });
        series.push({ name: 'J', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: kdjRes.j, symbol: 'none', lineStyle: { width: 1, color: COLORS.j }, z: 3 });
      } else if (sp === 'rsi' && rsiRes) {
        series.push({ name: 'RSI14', type: 'line', xAxisIndex: xi, yAxisIndex: yi, data: rsiRes, symbol: 'none', lineStyle: { width: 1.2, color: COLORS.rsi }, z: 3 });
      }
      subTop += SUB_HEIGHT + SUB_GAP;
    });

    // ── 推理分数：叠加到主图，共用主图 x 轴 + 右侧分数轴（scoreYI）──
    if (scoreSeries.length) {
      scoreSeries.forEach(sr => {
        const scoreMap = new Map(sr.points.map(p => [p.date, p.fusion]));
        series.push({
          name: `分数·${sr.model.slice(0, 10)}`, type: 'line', xAxisIndex: 0, yAxisIndex: scoreYI,
          data: bars.map(b => {
            const f = scoreMap.get(b.date);
            return f != null ? Number(f) : null;
          }),
          symbol: 'circle', symbolSize: 5, connectNulls: false,
          lineStyle: { width: 1.6, color: sr.color }, itemStyle: { color: sr.color, borderColor: '#fff', borderWidth: 1 },
          z: 6, emphasis: { scale: 1.3 },
        });
      });

      // 策略提醒标记（菱形，按 severity 着色，画在分数轴上）
      if (alerts.length) {
        const byDate = new Map(alerts.map(a => [a.date, a]));
        const alertData = bars
          .map((b, i) => {
            const a = byDate.get(b.date);
            if (!a) return null;
            const sr = scoreSeries[0];
            const f = a.score ?? sr?.points.find(p => p.date === b.date)?.fusion ?? null;
            return f == null ? null : { value: [i, Number(f)], a };
          })
          .filter(Boolean);
        if (alertData.length) {
          series.push({
            name: '策略提醒', type: 'scatter', xAxisIndex: 0, yAxisIndex: scoreYI,
            data: alertData, symbol: 'diamond', symbolSize: 12,
            itemStyle: {
              color: (p: any) => SEVERITY_COLOR[p.data.a.severity] ?? '#6366f1',
              borderColor: '#fff', borderWidth: 1,
            },
            label: { show: false },
            tooltip: {
              formatter: (p: any) => {
                const a = p.data.a;
                const sc = a.score != null ? ` · 分数 ${Number(a.score).toFixed(4)}` : '';
                return `<div><b>${dates[p.data.value[0]]}</b><br/><span style="color:${SEVERITY_COLOR[a.severity]};font-weight:bold">${a.message}</span>${sc}</div>`;
              },
            },
            z: 12,
          });
        }
      }

      // 参考线 + 默认黄金线 0.10：画在右侧分数轴上（主图 markLine）
      const visRef = refLines.filter(l => l.visible !== false);
      const hasGolden = visRef.some(l => Math.abs(l.value - DEFAULT_REF_LINE.value) < 1e-6);
      const allLines = hasGolden ? visRef : [DEFAULT_REF_LINE, ...visRef];
      const firstScore = series.find((s: any) => String(s.name).startsWith('分数·'));
      if (firstScore) {
        firstScore.markLine = {
          silent: true, symbol: 'none',
          data: allLines.map(l => ({
            yAxis: l.value,
            lineStyle: { color: l.color, type: 'dashed', width: 1.5 },
            label: { formatter: `${l.label} ${l.value >= 0 ? '+' : ''}${l.value.toFixed(2)}`, fontSize: 9, position: 'insideEndTop', color: l.color },
          })),
        };
      }
    }

    const legendData: string[] = [];
    if (ma5) legendData.push('MA5', 'MA10', 'MA20', 'MA60');
    scoreSeries.forEach(sr => legendData.push(`分数·${sr.model.slice(0, 10)}`));

    return {
      animation: false,
      backgroundColor: 'transparent',
      legend: legendData.length ? {
        show: true, top: 2, left: 68, itemWidth: 12, itemHeight: 8, itemGap: 8,
        textStyle: { fontSize: 9, color: '#64748b' },
        data: legendData,
      } : undefined,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#475569', fontSize: 10 } },
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e2e8f0',
        textStyle: { color: '#334155', fontSize: 11 },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      dataZoom: [
        { type: 'inside', xAxisIndex: xAxes.map((_, i) => i), start: 0, end: 100 },
        { type: 'slider', xAxisIndex: xAxes.map((_, i) => i), bottom: 2, height: 16, borderColor: '#e2e8f0', fillerColor: 'rgba(59,130,246,0.08)' },
      ],
      series,
    };
  }, [bars, config, overlays, height, signals, btEquity, scoreSeries, alerts, trades, refLines]);

  const onEvents = onBarClick ? {
    click: (params: any) => {
      const raw = params?.data;
      const idx = typeof raw === 'object' && raw?.value ? raw.value[0] : params?.dataIndex;
      const i = Number.isInteger(idx) && idx >= 0 && idx < bars.length ? idx : -1;
      if (i < 0) return;
      onBarClick(bars[i]);
    },
  } : undefined;

  return (
    <ReactECharts
      option={option}
      notMerge
      lazyUpdate
      style={{ width: '100%', height }}
      opts={{ renderer: 'canvas' }}
      onEvents={onEvents}
    />
  );
}

export const OVERLAY_COLORS = ['#0ea5e9', '#f97316', '#a855f7', '#14b8a6'];
