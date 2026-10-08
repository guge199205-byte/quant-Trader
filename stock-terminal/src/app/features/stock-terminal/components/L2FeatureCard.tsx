/** L2 微观结构因子卡：预测日前一交易日的 14 个推荐因子（值 + 全市场百分位）。
 *  hover 因子芯片显示该特征含义（desc）。 */

import { Tooltip } from 'antd';
import { Layers } from 'lucide-react';

/** 因子值格式化：量纲差异大，用有效位数自适应 */
export function fmtFactor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '--';
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(1).replace(/(\.[1-9]?)0+$/, '$1');
  if (a >= 1) return v.toFixed(3).replace(/\.?0+$/, '');
  return v.toFixed(4);
}

/** 因子强度徽标：14 因子均为正向 alpha，百分位越高信号越强 */
export function StrengthBadge({ pct }: { pct: number | null }) {
  if (pct == null) {
    return <span className="text-[8px] leading-none px-1 py-0.5 rounded bg-slate-50 text-slate-300 font-bold">--</span>;
  }
  if (pct >= 0.8) {
    return <span className="text-[8px] leading-none px-1 py-0.5 rounded bg-rose-100 text-rose-600 font-black">强·{Math.round(pct * 100)}%</span>;
  }
  if (pct >= 0.5) {
    return <span className="text-[8px] leading-none px-1 py-0.5 rounded bg-amber-100 text-amber-600 font-bold">中·{Math.round(pct * 100)}%</span>;
  }
  return <span className="text-[8px] leading-none px-1 py-0.5 rounded bg-slate-100 text-slate-400 font-bold">{Math.round(pct * 100)}%</span>;
}

export interface L2FeatureData {
  feature_date: string;
  factors: {
    name: string;
    label: string;
    category: string;
    icir: number;
    value: number | null;
    pct_rank: number | null;
    desc?: string;
  }[];
}

interface Props {
  l2: L2FeatureData | null;
  signalDate?: string | null;
}

/** 因子 hover 解释：含义 + 数值 + 百分位 + ICIR */
function FactorChip({ f }: { f: L2FeatureData['factors'][number] }) {
  const pct = f.pct_rank != null ? Math.round(f.pct_rank * 100) + '%' : '--';
  const content = (
    <div className="text-[10px] leading-relaxed max-w-60">
      <div className="font-black text-slate-800">
        {f.label} <span className="font-normal text-slate-400">· {f.category} · ICIR {f.icir}</span>
      </div>
      <div className="mt-0.5 text-slate-600">{f.desc || '暂无该因子说明'}</div>
      <div className="mt-1 text-slate-400">{`值 ${fmtFactor(f.value)} · 全市场百分位 ${pct}（越高=信号越强）`}</div>
    </div>
  );
  return (
    <Tooltip title={content} placement="top" mouseEnterDelay={0.15}>
      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-50 hover:bg-slate-200 border border-slate-100 transition-colors cursor-help">
        <span className="text-[9px] text-slate-500">{f.label}</span>
        <span className="text-[9px] font-bold text-slate-700">{fmtFactor(f.value)}</span>
        <StrengthBadge pct={f.pct_rank} />
        <span className="w-3 h-3 rounded-full bg-slate-200 text-slate-500 text-[7px] leading-[12px] text-center font-black">?</span>
      </span>
    </Tooltip>
  );
}

export function L2FeatureCard({ l2, signalDate }: Props) {
  // 因子按类别分组（保留推荐顺序）
  const catOrder = Array.from(new Set((l2?.factors ?? []).map(f => f.category)));
  const byCat = catOrder.map(cat => ({
    cat,
    items: (l2?.factors ?? []).filter(f => f.category === cat),
  }));

  return (
    <div className="bg-white/70 rounded-2xl border border-slate-100 px-4 py-2.5">
      <div className="flex items-center justify-between pb-1.5 mb-1.5 border-b border-slate-100">
        <div className="flex items-center gap-1.5">
          <Layers className="w-3 h-3 text-rose-500" />
          <span className="text-[11px] font-black text-slate-700">L2 微观结构因子</span>
          <span className="text-[9px] text-slate-400 font-bold">(hover 因子查看含义)</span>
        </div>
        <span className="text-[9px] font-bold text-slate-400">
          {'预测日 '}
          <b className="text-slate-500">{signalDate ?? '--'}</b>
          {' → 特征日 '}
          <b className="text-slate-500">{l2?.feature_date ?? '--'}</b>
        </span>
      </div>

      {l2 ? (
        <div className="flex flex-col gap-1">
          {byCat.map(({ cat, items }) => (
            <div key={cat} className="flex items-center gap-1.5 flex-wrap">
              <span className="w-10 shrink-0 text-[9px] font-black text-slate-400">{cat}</span>
              {items.map(f => <FactorChip key={f.name} f={f} />)}
            </div>
          ))}
          <p className="text-[9px] text-slate-400 pt-0.5">
            14 个推荐因子（VPIN/时段/资金流等微观结构，单因子 ICIR 0.16~0.56）· 徽标=当日全市场百分位（越高=因子越强，正向信号）
          </p>
        </div>
      ) : (
        <p className="py-2.5 text-center text-[10px] text-slate-400">该股无推理信号，无预测日前日 L2 特征</p>
      )}
    </div>
  );
}