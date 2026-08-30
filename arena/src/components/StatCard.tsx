import { ReactNode } from 'react';

/** 统计卡：k = 上标标签，v = 主值，sub = 副说明（可选） */
export default function StatCard({
  k,
  v,
  sub,
  className,
}: {
  k: string;
  v: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className="stat-card">
      <div className="k">{k}</div>
      <div className={`v ${className ?? ''}`}>{v}</div>
      {sub != null && <div className="s">{sub}</div>}
    </div>
  );
}
