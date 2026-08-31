import { useCallback, useMemo, useRef, useState } from 'react';
import { Group } from '@visx/group';
import { GridRows, GridColumns } from '@visx/grid';
import { Area, LinePath } from '@visx/shape';
import { scaleTime, scaleLinear } from '@visx/scale';
import { curveMonotoneX } from '@visx/curve';
import { LinearGradient } from '@visx/gradient';
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
  /** 名义基准金额（如实盘分账 ¥10 万）→ hover 时换算金额盈亏 */
  notional?: number;
}

export interface BenchLine {
  id: string;
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
  const [hover, setHover] = useState<{ x: number; y: number; label: string; id: string; v: number; t: number } | null>(null);

  // 底部图例: 选中某模型 → 该线实线、其他虚线淡化; null = 全部实线
  const [focus, setFocus] = useState<string | null>(null);

  // 时间轴交互: 拖拽平移 · 滚轮缩放 · 双击复位(scale=1 全览, offsetPx=窗口相对左端偏移)
  const [view, setView] = useState({ offsetPx: 0, scale: 1 });
  const dragRef = useRef<{ startX: number; startOffset: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // 手势防护: React 合成 onWheel/onTouchMove 在 root 上是 passive, preventDefault 无效 →
  // 触摸板横向滑动会被浏览器当成"前进/后退"手势触发整页导航(看起来像刷新)。
  // 用原生 non-passive 监听在元素上拦截, 图表内的滑动/拖拽绝不落到页面。
  const gestureGuard = useCallback((el: SVGRectElement | null) => {
    if (!el) return;
    const prevent = (e: Event) => e.preventDefault();
    el.addEventListener('wheel', prevent, { passive: false });
    el.addEventListener('touchstart', prevent, { passive: false });
    el.addEventListener('touchmove', prevent, { passive: false });
  }, []);

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

  // 全数据时间域(X 轴窗口由 view 平移/缩放决定; Y 域按窗口内数据在渲染时算)
  const domain = useMemo(() => {
    const all = [...display.lines, ...(display.bench ? [display.bench] : [])];
    if (!all.length) return { tMin: 0, tMax: 1 };
    let tMin = Infinity, tMax = -Infinity;
    for (const l of all) {
      for (const p of l.points) {
        tMin = Math.min(tMin, p.t);
        tMax = Math.max(tMax, p.t);
      }
    }
    if (!Number.isFinite(tMin) || tMin === tMax) { tMin = 0; tMax = 1; }
    return { tMin, tMax };
  }, [display]);

  if (!lines.length) {
    return <div className="loading">暂无净值数据</div>;
  }

  return (
    <div style={{ height: typeof height === 'number' ? `${height}px` : height, width: '100%', position: 'relative' }}>
      <div
        style={{
          position: 'absolute', top: -2, right: 2, fontSize: 10, color: '#999',
          userSelect: 'none', pointerEvents: 'none', zIndex: 1,
        }}
      >
        拖拽平移 · 滚轮缩放 · 双击复位
      </div>
      <ParentSize>
        {({ width, height: h }) => {
          const iw = Math.max(width - margin.left - margin.right, 10);
          const ih = Math.max(h - margin.top - margin.bottom, 10);

          // 时间轴窗口: scale=1 全览; 放大后 offsetPx 平移(0=最左, max=最右, 渲染时防越界 clamp)
          const tSpan = domain.tMax - domain.tMin || 1;
          const pxPerMs = (iw / tSpan) * view.scale;
          const maxOffset = Math.max(0, iw * (view.scale - 1));
          const offsetPx = Math.min(view.offsetPx, maxOffset);
          const winStart = domain.tMin + offsetPx / pxPerMs;
          const winEnd = Math.min(winStart + tSpan / view.scale, domain.tMax);
          const xScale = scaleTime({ domain: [winStart, winEnd], range: [0, iw] });

          // Y 域: 当前窗口内数据自适应(+7% 边距; 窗口内无点回退全范围)
          const yLines = [...display.lines, ...(display.bench ? [display.bench] : [])];
          let vMin = Infinity, vMax = -Infinity;
          for (const l of yLines) {
            for (const p of l.points) {
              if (p.t >= winStart && p.t <= winEnd) {
                vMin = Math.min(vMin, p.v);
                vMax = Math.max(vMax, p.v);
              }
            }
          }
          if (!Number.isFinite(vMin)) {
            for (const l of yLines) {
              for (const p of l.points) {
                vMin = Math.min(vMin, p.v);
                vMax = Math.max(vMax, p.v);
              }
            }
          }
          if (!Number.isFinite(vMin) || vMin === vMax) { vMin = 0; vMax = 1; }
          if (mode === 'dollar' && vMin < 0) vMin = 0; // 绝对净值不画负区
          const padY = (vMax - vMin) * 0.07 || 1;
          const yScale = scaleLinear({ domain: [vMin - padY, vMax + padY], range: [ih, 0] });

          // X 轴刻度格式: 窗口 ≤1.2 天 → HH:mm; ≤8 天 → MM-DD HH:mm; 更久 → MM-DD
          const spanDays = (winEnd - winStart) / 86400000;
          const tickFmt =
            spanDays <= 1.2 ? 'HH:mm' : spanDays <= 8 ? 'MM-DD HH:mm' : 'MM-DD';
          // 实盘数据为 +08:00（北京时间）；本机可能非北京时区，格式化时统一按 +8h 显示
          const fmtTs = (t: number) => {
            const d = new Date(t + 8 * 3600000);
            if (tickFmt === 'MM-DD HH:mm') {
              return `${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)}`;
            }
            if (tickFmt === 'HH:mm') return d.toISOString().slice(11, 16);
            return dayjs(t).format('MM-DD');
          };

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
                  tickFormat={(v) => fmtTs((v as Date).getTime())}
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
                  tickFormat={(v) =>
                    mode === 'pct'
                      ? `${Number(v).toFixed(1)}%`
                      : fmtMoney(Number(v), currency, 0)
                  }
                />
                <defs>
                  {/* 每线渐变垫层: 线色向下渐隐(柔化视觉); 悬停/聚焦时线本身加光晕 */}
                  {display.lines.map((l, i) => (
                    <LinearGradient key={`g-${l.id}`} id={`grad-${i}`}
                      from={l.color} to={l.color} fromOpacity={0.25} toOpacity={0} vertical />
                  ))}
                  {display.bench && (
                    <LinearGradient id="grad-bench"
                      from={display.bench.color} to={display.bench.color} fromOpacity={0.16} toOpacity={0} vertical />
                  )}
                  <filter id="line-glow" x="-30%" y="-30%" width="160%" height="160%">
                    <feDropShadow dx="0" dy="1.5" stdDeviation="2.5" floodColor="#000" floodOpacity="0.28" />
                  </filter>
                  <clipPath id="chart-clip">
                    <rect x={0} y={0} width={iw} height={ih} />
                  </clipPath>
                </defs>

                <Group clipPath="url(#chart-clip)">
                  {/* 基准线: 聚焦某模型时退到 0.5 淡度, 其余默认 0.9 */}
                  {display.bench && (() => {
                    const active = !focus || hover?.id === display.bench.id;
                    const hl = hover?.id === display.bench.id;
                    const baseV = display.bench.points.reduce((m, p) => Math.min(m, p.v), Infinity);
                    return (
                      <Group opacity={hover ? (hl ? 1 : 0.35) : focus ? 0.55 : 0.9}>
                        <Area
                          data={display.bench.points}
                          x={(p: { t: number; v: number }) => xScale(p.t) ?? 0}
                          y0={() => yScale(baseV) ?? 0}
                          y1={(p: { t: number; v: number }) => yScale(p.v) ?? 0}
                          fill="url(#grad-bench)"
                          curve={curveMonotoneX}
                        />
                        <LinePath
                          data={display.bench.points}
                          x={(p) => xScale(p.t) ?? 0}
                          y={(p) => yScale(p.v) ?? 0}
                          stroke={display.bench.color}
                          strokeWidth={hl ? 2.4 : 1.2}
                          strokeDasharray={active ? '5 4' : '3 4'}
                          filter={hl ? 'url(#line-glow)' : undefined}
                          curve={curveMonotoneX}
                        />
                      </Group>
                    );
                  })()}

                  {/* 模型线: 平滑曲线 + 渐变垫层; 聚焦时未选模型虚线淡化, hover 时最近线加粗光晕 */}
                  {display.lines.map((l, i) => {
                    const focusActive = !focus || l.id === focus;          // 图例选中判定
                    const hl = hover?.id === l.id;                          // 悬停高亮判定
                    const winPts = l.points.filter((p) => p.t >= winStart && p.t <= winEnd);
                    const baseV = winPts.length ? Math.min(...winPts.map((p) => p.v)) : l.points[0]?.v ?? 0;
                    return (
                      <Group key={l.id} opacity={hover ? (hl ? 1 : 0.35) : focusActive ? 1 : 0.45}>
                        <Area
                          data={l.points}
                          x={(p: { t: number; v: number }) => xScale(p.t) ?? 0}
                          y0={() => yScale(baseV) ?? 0}
                          y1={(p: { t: number; v: number }) => yScale(p.v) ?? 0}
                          fill={`url(#grad-${i})`}
                          curve={curveMonotoneX}
                        />
                        <LinePath
                          data={l.points}
                          x={(p) => xScale(p.t) ?? 0}
                          y={(p) => yScale(p.v) ?? 0}
                          stroke={l.color}
                          strokeWidth={hl ? 2.8 : focusActive ? 2 : 1.3}
                          strokeDasharray={focusActive ? undefined : '6 5'}
                          filter={hl ? 'url(#line-glow)' : undefined}
                          curve={curveMonotoneX}
                        />
                      </Group>
                    );
                  })}
                </Group>
                {display.lines.map((l) => {
                  const last = l.points[l.points.length - 1];
                  if (!last) return null;
                  const x = xScale(last.t) ?? 0;
                  const y = yScale(last.v) ?? 0;
                  // 末端标签: 圆点右侧显示 模型名+值; 靠右(>60%宽)时放左侧右对齐, 防溢出
                  const onRight = x > iw * 0.6;
                  const anchor = onRight ? 'end' : 'start';
                  const lx = onRight ? x - 10 : x + 10;
                  const focusActive = !focus || l.id === focus;
                  return (
                    <Group key={`end-${l.id}`} opacity={hover ? (hover.id === l.id ? 1 : 0.3) : focusActive ? 1 : 0.45}>
                      <circle cx={x} cy={y} r={5.5} fill={l.color} stroke="#fff" strokeWidth={2} />
                      <text x={lx} y={y - 8} fill={l.color} fontSize={10} fontWeight={700} textAnchor={anchor}
                        fontFamily="'Courier New', monospace">
                        {l.label}
                      </text>
                      <text x={lx} y={y + 7} fill={l.color} fontSize={10} textAnchor={anchor}
                        fontFamily="'Courier New', monospace">
                        {mode === 'pct' ? `${Number(last.v).toFixed(1)}%` : fmtMoney(last.v, currency, 0)}
                      </text>
                    </Group>
                  );
                })}
                {hover &&
                  (() => {
                    const hl = display.lines.find((l) => l.id === hover.id);
                    const base = hl && hl.points.length ? hl.points[0].v : null;
                    const chg = base ? ((hover.v - base) / base) * 100 : null;
                    // 金额盈亏: 有名义基准时按收益率 × 基准换算（实盘分账 = ¥10 万）
                    const pnlAmt = hl?.notional && base ? ((hover.v / base) - 1) * hl.notional : null;
                    const pnlColor = (v: number | null) => (v == null || v >= 0 ? '#c0392b' : '#27ae60');
                    const tx = Math.min(hover.x + 6, iw - 212);
                    const ty = Math.max(hover.y - 58, 2);
                    const isBench = display.bench?.id === hover.id;
                    return (
                      <Group>
                        <line x1={hover.x} x2={hover.x} y1={0} y2={ih} stroke="rgba(0,0,0,0.25)" strokeDasharray="2 3" />
                        <rect x={tx} y={ty} width={206} height={58} fill="#fff" stroke="#000" strokeWidth={1} rx={4} />
                        <text x={tx + 6} y={ty + 13} fill="#000" fontSize={10} fontWeight={700}
                          fontFamily="'Courier New', monospace">
                          {hover.label} · {fmtTs(hover.t)}
                        </text>
                        <text x={tx + 6} y={ty + 27} fill="#000" fontSize={10}
                          fontFamily="'Courier New', monospace">
                          {mode === 'pct' ? `${Number(hover.v).toFixed(1)}%` : fmtMoney(hover.v, currency, 0)}
                          {chg != null && (
                            <tspan fill={pnlColor(chg)}>
                              {'  '}{chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
                            </tspan>
                          )}
                        </text>
                        {pnlAmt != null && (
                          <text x={tx + 6} y={ty + 40} fill={pnlColor(pnlAmt)} fontSize={10} fontWeight={700}
                            fontFamily="'Courier New', monospace">
                            盈亏 {pnlAmt >= 0 ? '+' : ''}{fmtMoney(pnlAmt, currency, 0)}
                          </text>
                        )}
                        <text x={tx + 6} y={ty + 53} fill="#666" fontSize={9}
                          fontFamily="'Courier New', monospace">
                          {isBench ? '基准指数' : hover.label === '总账户' ? '通达信桥实时总资产' : '虚拟净值（¥10万起步）'}
                        </text>
                      </Group>
                    );
                  })()}
              </Group>
              {width > 8 && (
                <rect
                  ref={gestureGuard}
                  x={margin.left} y={margin.top} width={iw} height={ih}
                  fill="transparent"
                  style={{ cursor: dragging ? 'grabbing' : 'grab', touchAction: 'none', userSelect: 'none' }}
                  onDoubleClick={() => setView({ offsetPx: 0, scale: 1 })}
                  onWheel={(e) => {
                    e.preventDefault();
                    const rect = (e.currentTarget as SVGRectElement).getBoundingClientRect();
                    const mx = e.clientX - rect.left;
                    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
                    const scale = Math.max(1, Math.min(50, view.scale * factor));
                    // 以鼠标下时间点为锚,缩放后位置不变
                    const tAtMx = xScale.invert(mx).getTime();
                    const pxPerMs2 = (iw / tSpan) * scale;
                    const offsetPx = Math.max(0, Math.min(iw * (scale - 1), (tAtMx - domain.tMin) * pxPerMs2 - mx));
                    setView({ scale, offsetPx });
                  }}
                  onMouseDown={(e) => {
                    dragRef.current = { startX: e.clientX, startOffset: view.offsetPx };
                    setDragging(true);
                  }}
                  onMouseMove={(e) => {
                    const rect = (e.currentTarget as SVGRectElement).getBoundingClientRect();
                    const relX = e.clientX - rect.left;
                    const relY = e.clientY - rect.top;
                    if (dragRef.current) {
                      const dx = e.clientX - dragRef.current.startX;
                      setView((v) => ({
                        ...v,
                        offsetPx: Math.max(0, Math.min(iw * (v.scale - 1), dragRef.current!.startOffset - dx)),
                      }));
                      return; // 拖动中不触发 hover
                    }
                    // 2D 最近吸附: 鼠标停在哪根线附近, 就高亮哪根并显示它的信息
                    const candidates = [...display.lines, ...(display.bench ? [display.bench] : [])];
                    let best: { d: number; label: string; id: string; v: number; t: number; x: number; y: number } | null = null;
                    for (const l of candidates) {
                      for (const p of l.points) {
                        const px = xScale(p.t) ?? -1e9;
                        const py = yScale(p.v) ?? -1e9;
                        const d = Math.hypot(px - relX, py - relY);
                        if (!best || d < best.d) best = { d, label: l.label, id: l.id, v: p.v, t: p.t, x: px, y: py };
                      }
                    }
                    if (best && best.d < 70) {
                      setHover({ x: best.x, y: best.y, label: best.label, id: best.id, v: best.v, t: best.t });
                    } else {
                      setHover(null);
                    }
                  }}
                  onMouseUp={() => { dragRef.current = null; setDragging(false); }}
                  onMouseLeave={() => { dragRef.current = null; setDragging(false); setHover(null); }}
                  onTouchStart={(e) => {
                    const t = e.touches[0];
                    dragRef.current = { startX: t.clientX, startOffset: view.offsetPx };
                    setDragging(true);
                  }}
                  onTouchMove={(e) => {
                    e.preventDefault();
                    if (!dragRef.current) return;
                    const dx = e.touches[0].clientX - dragRef.current.startX;
                    setView((v) => ({
                      ...v,
                      offsetPx: Math.max(0, Math.min(iw * (v.scale - 1), dragRef.current!.startOffset - dx)),
                    }));
                  }}
                  onTouchEnd={() => { dragRef.current = null; setDragging(false); }}
                />
              )}
            </svg>
          );
        }}
      </ParentSize>
      {/* 底部图例: 点选模型 → 该线实线, 其他虚线淡化; "全部"恢复 */}
      <div style={{
        display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap',
        paddingLeft: margin.left, alignItems: 'center', userSelect: 'none',
      }}>
        <button
          onClick={() => setFocus(null)}
          style={legendChip(focus === null, '#333')}
        >
          全部
        </button>
        {lines.map((l) => (
          <button
            key={l.id}
            onClick={() => setFocus(focus === l.id ? null : l.id)}
            style={legendChip(focus === l.id, l.color)}
          >
            <span style={{ display: 'inline-block', width: 14, height: 0, borderTop: `2px solid ${l.color}`, verticalAlign: 'middle' }} />
            {l.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** 图例 chip 样式: 选中 = 线色底白字, 未选 = 白底灰字 */
const legendChip = (active: boolean, color: string): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 5,
  padding: '2px 10px', border: `1px solid ${active ? color : '#ccc'}`,
  borderRadius: 12, background: active ? color : '#fff',
  color: active ? '#fff' : '#555', fontSize: 11, cursor: 'pointer',
});

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
    id: `bench-${label}`,
    label,
    color,
    points: points.map((p) => ({ t: dayjs(p.time).valueOf(), v: p.close })),
  };
};
