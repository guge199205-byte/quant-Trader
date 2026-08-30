import { useMemo, useState } from 'react';
import { Group } from '@visx/group';
import { GridRows, GridColumns } from '@visx/grid';
import { LinePath } from '@visx/shape';
import { scaleTime, scaleLinear } from '@visx/scale';
import { curveLinear } from '@visx/curve';
import { AxisBottom, AxisLeft } from '@visx/axis';
import { ParentSize } from '@visx/responsive';
import dayjs from 'dayjs';
import { EquityPoint } from '../api/client';
import { fmtMoney } from '../utils/format';

export interface ChartLine {
  id: string;
  label: string;
  color: string;
  points: { t: number; v: number }[]; // v 为绝对净值
}

export interface BenchLine {
  label: string;
  color: string;
  points: { t: number; v: number }[]; // v 为指数点位
}

const margin = { top: 16, right: 16, bottom: 34, left: 62 };

/** 多模型净值对比图（visx，浅色终端风）。
 *  mode: 'pct' = 归一化 100 起点（多市场/多币种可对比，基准天然同轴）；
 *        'dollar' = 绝对净值（同币种单市场对比）。
 *  timeRange: 'all' | '5d' 控制时间窗（5d = 最近 5 个交易日）。 */
export default function EquityChart({
  lines,
  benchmark,
  currency = '$',
  mode = 'pct',
  timeRange = 'all',
  height = 380,
}: {
  lines: ChartLine[];
  benchmark?: BenchLine | null;
  currency?: string;
  mode?: 'pct' | 'dollar';
  timeRange?: 'all' | '5d';
  /** 数字 = 固定 px；字符串 = 任意 CSS 值（如 clamp(...) 响应式高度） */
  height?: number | string;
}) {
  const [hover, setHover] = useState<{ x: number; y: number; label: string; v: number } | null>(null);

  // 时间窗裁剪 + 归一化（pct）/ 绝对（dollar）
  const display = useMemo(() => {
    const windowed = (pts: { t: number; v: number }[]) =>
      timeRange === '5d' && pts.length > 5 ? pts.slice(-5) : pts;
    const toDisplay = (pts: { t: number; v: number }[]) => {
      const w = windowed(pts);
      if (mode === 'dollar') return w;
      const base = w[0]?.v || 1;
      return w.map((p) => ({ t: p.t, v: (p.v / base) * 100 }));
    };
    return {
      lines: lines.map((l) => ({ ...l, points: toDisplay(l.points) })),
      bench: benchmark && mode === 'pct' ? { ...benchmark, points: toDisplay(benchmark.points) } : null,
    };
  }, [lines, benchmark, mode, timeRange]);

  const domain = useMemo(() => {
    const all = [...display.lines, ...(display.bench ? [display.bench] : [])];
    if (!all.length) return { tMin: 0, tMax: 1, vMin: 0, vMax: 1 };
    let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
    for (const l of all) {
      for (const p of l.points) {
        tMin = Math.min(tMin, p.t);
        tMax = Math.max(tMax, p.t);
        vMin = Math.min(vMin, p.v);
        vMax = Math.max(vMax, p.v);
      }
    }
    if (!Number.isFinite(vMin) || vMin === vMax) { vMin = 0; vMax = 1; }
    if (mode === 'dollar' && vMin < 0) vMin = 0; // 绝对净值不画负区
    const pad = (vMax - vMin) * 0.07 || 1;
    return { tMin, tMax, vMin: vMin - pad, vMax: vMax + pad };
  }, [display, mode]);

  if (!lines.length) {
    return <div className="loading">暂无净值数据</div>;
  }

  return (
    <div style={{ height: typeof height === 'number' ? `${height}px` : height, width: '100%' }}>
      <ParentSize>
        {({ width, height: h }) => {
          const iw = Math.max(width - margin.left - margin.right, 10);
          const ih = Math.max(h - margin.top - margin.bottom, 10);
          const xScale = scaleTime({ domain: [domain.tMin, domain.tMax], range: [0, iw] });
          const yScale = scaleLinear({ domain: [domain.vMin, domain.vMax], range: [ih, 0] });

          return (
            <svg width={width} height={h} role="img" aria-label="净值对比图">
              <Group left={margin.left} top={margin.top}>
                <GridRows scale={yScale} width={iw} stroke="rgba(0,0,0,0.06)" strokeDasharray="3 4" />
                <GridColumns scale={xScale} height={ih} stroke="rgba(0,0,0,0.06)" strokeDasharray="3 4" />
                <AxisBottom
                  top={ih}
                  scale={xScale}
                  numTicks={Math.min(10, Math.floor(iw / 90))}
                  stroke="rgba(0,0,0,0.2)"
                  tickStroke="rgba(0,0,0,0.2)"
                  tickLabelProps={() => ({
                    fill: '#666', fontSize: 10, textAnchor: 'middle', dy: 8,
                    fontFamily: "'Courier New', monospace",
                  })}
                  tickFormat={(v) => dayjs(v as Date).format('MM-DD')}
                />
                <AxisLeft
                  scale={yScale}
                  numTicks={6}
                  stroke="rgba(0,0,0,0.2)"
                  tickStroke="rgba(0,0,0,0.2)"
                  tickLabelProps={() => ({
                    fill: '#666', fontSize: 10, textAnchor: 'end', dx: -6,
                    fontFamily: "'Courier New', monospace",
                  })}
                  tickFormat={(v) => fmtMoney(Number(v), currency, 0)}
                />
                {display.bench && (
                  <LinePath
                    data={display.bench.points}
                    x={(p) => xScale(p.t) ?? 0}
                    y={(p) => yScale(p.v) ?? 0}
                    stroke={display.bench.color}
                    strokeWidth={1.2}
                    strokeDasharray="5 4"
                    curve={curveLinear}
                  />
                )}
                {display.lines.map((l) => (
                  <LinePath
                    key={l.id}
                    data={l.points}
                    x={(p) => xScale(p.t) ?? 0}
                    y={(p) => yScale(p.v) ?? 0}
                    stroke={l.color}
                    strokeWidth={1.8}
                    curve={curveLinear}
                  />
                ))}
                {display.lines.map((l) => {
                  const last = l.points[l.points.length - 1];
                  if (!last) return null;
                  const x = xScale(last.t) ?? 0;
                  const y = yScale(last.v) ?? 0;
                  return (
                    <Group key={`end-${l.id}`}>
                      <circle cx={x} cy={y} r={3.2} fill={l.color} stroke="#fff" strokeWidth={1.5} />
                      <text x={x + 8} y={y - 7} fill={l.color} fontSize={11} fontWeight={700}
                        fontFamily="'Courier New', monospace">
                        {mode === 'pct' ? Number(last.v).toFixed(1) : fmtMoney(last.v, currency, 0)}
                      </text>
                    </Group>
                  );
                })}
                {hover && (
                  <Group>
                    <line x1={hover.x} x2={hover.x} y1={0} y2={ih} stroke="rgba(0,0,0,0.25)" strokeDasharray="2 3" />
                    <rect x={Math.min(hover.x + 6, iw - 136)} y={Math.max(hover.y - 36, 0)} width={130} height={30}
                      fill="#fff" stroke="#000" strokeWidth={1} />
                    <text x={Math.min(hover.x + 12, iw - 130)} y={Math.max(hover.y - 16, 12)} fill="#000" fontSize={10}
                      fontFamily="'Courier New', monospace">
                      {hover.label}: {mode === 'pct' ? Number(hover.v).toFixed(1) : fmtMoney(hover.v, currency, 0)}
                    </text>
                  </Group>
                )}
              </Group>
              {width > 8 && (
                <rect
                  x={margin.left} y={margin.top} width={iw} height={ih}
                  fill="transparent"
                  onMouseMove={(e) => {
                    const rect = (e.currentTarget as SVGRectElement).getBoundingClientRect();
                    const relX = e.clientX - rect.left;
                    const t = xScale.invert(relX).getTime();
                    let best: { d: number; label: string; v: number } | null = null;
                    for (const l of display.lines) {
                      for (const p of l.points) {
                        const d = Math.abs(p.t - t);
                        if (!best || d < best.d) best = { d, label: l.label, v: p.v };
                      }
                    }
                    if (best && best.d < 5 * 86400000) {
                      setHover({ x: relX, y: yScale(best.v), label: best.label, v: best.v });
                    }
                  }}
                  onMouseLeave={() => setHover(null)}
                />
              )}
            </svg>
          );
        }}
      </ParentSize>
    </div>
  );
}

/** equity 序列 → 图表线（绝对净值，归一化在组件内完成） */
export const toChartLine = (
  id: string,
  label: string,
  color: string,
  points: EquityPoint[],
): ChartLine => ({
  id,
  label,
  color,
  points: points.map((p) => ({ t: dayjs(p.date).valueOf(), v: p.equity })),
});

/** 指数序列 → 基准线 */
export const toBenchLine = (
  label: string,
  color: string,
  points: { time: string; close: number }[],
): BenchLine | null => {
  if (!points.length) return null;
  return {
    label,
    color,
    points: points.map((p) => ({ t: dayjs(p.time).valueOf(), v: p.close })),
  };
};
