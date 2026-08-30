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

/** 多模型净值对比图（visx）。归一化 = 各自起点 100，可叠加基准虚线（基准天然对齐 100 起点）。
 *  传入绝对净值；归一化在组件内部完成，保证与基准同轴。 */
export default function EquityChart({
  lines,
  benchmark,
  currency = '$',
  height = 380,
}: {
  lines: ChartLine[];
  benchmark?: BenchLine | null;
  currency?: string;
  height?: number;
}) {
  const [normalize, setNormalize] = useState(true);
  const [hover, setHover] = useState<{ x: number; y: number; label: string; v: number } | null>(null);

  // 按展示模式换算显示值：归一化 100 起点 / 绝对净值
  const display = useMemo(() => {
    const norm = (pts: { t: number; v: number }[]) => {
      const base = pts[0]?.v || 1;
      return pts.map((p) => ({ t: p.t, v: (p.v / base) * 100 }));
    };
    const raw = (pts: { t: number; v: number }[]) => pts.map((p) => ({ t: p.t, v: p.v }));
    return {
      lines: lines.map((l) => ({ ...l, points: normalize ? norm(l.points) : raw(l.points) })),
      bench:
        benchmark && normalize
          ? { ...benchmark, points: norm(benchmark.points) }
          : null,
    };
  }, [lines, benchmark, normalize]);

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
    const pad = (vMax - vMin) * 0.07 || 1;
    return { tMin, tMax, vMin: vMin - pad, vMax: vMax + pad };
  }, [display]);

  if (!lines.length) {
    return <div className="loading">暂无净值数据</div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, padding: '10px 12px 0', alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="faint" style={{ fontSize: 10.5, letterSpacing: '0.1em' }}>净值对比</span>
        <span className={`chip ${normalize ? 'active' : ''}`} onClick={() => setNormalize(true)}>归一化 100</span>
        <span className={`chip ${!normalize ? 'active' : ''}`} onClick={() => setNormalize(false)}>绝对净值</span>
        <span style={{ flex: 1 }} />
        {lines.map((l) => (
          <span key={l.id} className="faint" style={{ fontSize: 11 }}>
            <span style={{ color: l.color }}>▬</span> {l.label}
          </span>
        ))}
        {display.bench && (
          <span className="faint" style={{ fontSize: 11 }}>
            <span style={{ color: display.bench.color }}>- -</span> {display.bench.label}
          </span>
        )}
      </div>
      <div style={{ height, width: '100%' }}>
        <ParentSize>
          {({ width, height: h }) => {
            const iw = Math.max(width - margin.left - margin.right, 10);
            const ih = Math.max(h - margin.top - margin.bottom, 10);
            const xScale = scaleTime({ domain: [domain.tMin, domain.tMax], range: [0, iw] });
            const yScale = scaleLinear({ domain: [domain.vMin, domain.vMax], range: [ih, 0] });

            return (
              <svg width={width} height={h} role="img" aria-label="净值对比图">
                <Group left={margin.left} top={margin.top}>
                  <GridRows scale={yScale} width={iw} stroke="rgba(255,255,255,0.05)" strokeDasharray="3 4" />
                  <GridColumns scale={xScale} height={ih} stroke="rgba(255,255,255,0.05)" strokeDasharray="3 4" />
                  <AxisBottom
                    top={ih}
                    scale={xScale}
                    numTicks={Math.min(10, Math.floor(iw / 90))}
                    stroke="rgba(255,255,255,0.15)"
                    tickStroke="rgba(255,255,255,0.15)"
                    tickLabelProps={() => ({
                      fill: '#4d5a6b', fontSize: 10, textAnchor: 'middle', dy: 8,
                    })}
                    tickFormat={(v) => dayjs(v as Date).format('MM-DD')}
                  />
                  <AxisLeft
                    scale={yScale}
                    numTicks={6}
                    stroke="rgba(255,255,255,0.15)"
                    tickStroke="rgba(255,255,255,0.15)"
                    tickLabelProps={() => ({
                      fill: '#4d5a6b', fontSize: 10, textAnchor: 'end', dx: -6,
                    })}
                    tickFormat={(v) => (normalize ? `${Number(v).toFixed(0)}` : fmtMoney(Number(v), currency, 0))}
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
                        <circle cx={x} cy={y} r={3.2} fill={l.color} stroke="#0a0d12" strokeWidth={1.5} />
                        <text x={x + 8} y={y - 7} fill={l.color} fontSize={11} fontWeight={700}>
                          {normalize ? Number(last.v).toFixed(1) : fmtMoney(last.v, currency, 0)}
                        </text>
                      </Group>
                    );
                  })}
                  {hover && (
                    <Group>
                      <line x1={hover.x} x2={hover.x} y1={0} y2={ih} stroke="rgba(255,255,255,0.2)" strokeDasharray="2 3" />
                      <rect x={Math.min(hover.x + 6, iw - 136)} y={Math.max(hover.y - 36, 0)} width={130} height={30} rx={4}
                        fill="#0f141b" stroke="#2a3747" />
                      <text x={Math.min(hover.x + 12, iw - 130)} y={Math.max(hover.y - 16, 12)} fill="#d8dee7" fontSize={10}>
                        {hover.label}: {Number(hover.v).toFixed(1)}
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
