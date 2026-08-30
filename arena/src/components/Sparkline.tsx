import { useMemo } from 'react';
import { EquityPoint } from '../api/client';

/** 迷你净值走势（纯 SVG polyline，卡内小图，不引入 visx） */
export default function Sparkline({
  points,
  width = 200,
  height = 34,
}: {
  points: EquityPoint[];
  width?: number;
  height?: number;
}) {
  const path = useMemo(() => {
    if (points.length < 2) return '';
    const vals = points.map((p) => p.equity);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const stepX = width / (points.length - 1);
    return points
      .map((p, i) => {
        const x = i * stepX;
        const y = height - 3 - ((p.equity - min) / span) * (height - 6);
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }, [points, width, height]);

  if (!path) return <div className="spark faint">—</div>;
  const up = points[points.length - 1].equity >= points[0].equity;
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <path d={path} fill="none" stroke={up ? 'var(--up)' : 'var(--down)'} strokeWidth="1.6" />
    </svg>
  );
}
