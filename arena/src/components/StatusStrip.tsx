import { MarketId, OverviewRow, marketMeta } from '../api/client';
import { fmtMoney, fmtNum, fmtPct } from '../utils/format';

/** Live 顶部状态条：市场 + 数据日期 + agent 数 + 总权益 + 最佳收益 + 基准 */
export default function StatusStrip({
  market,
  rows,
  bench,
  benchLabel,
}: {
  market: MarketId;
  rows: OverviewRow[];
  bench: { time: string; close: number }[] | null;
  benchLabel: string;
}) {
  const meta = marketMeta(market);
  const withSummary = rows.filter((r) => r.summary);
  const totalEquity = withSummary.reduce((s, r) => s + (r.summary?.end_equity ?? 0), 0);
  const totalStart = withSummary.reduce((s, r) => s + (r.summary?.start_equity ?? 0), 0);
  const overall = totalStart ? (totalEquity - totalStart) / totalStart : 0;
  const best = [...withSummary].sort((a, b) => (b.summary?.total_return ?? 0) - (a.summary?.total_return ?? 0))[0];
  const maxDate = rows.reduce((m, r) => (r.latest_date && r.latest_date > m ? r.latest_date : m), '');
  const benchLast = bench && bench.length ? bench[bench.length - 1] : null;
  const benchPrev = bench && bench.length > 1 ? bench[bench.length - 2] : null;
  const benchChg = benchLast && benchPrev?.close ? benchLast.close / benchPrev.close - 1 : null;

  return (
    <div className="status-strip">
      <div className="status-cell">
        <div className="k">市场</div>
        <div className="v" style={{ color: `var(--${market})` }}>{meta.name}</div>
      </div>
      <div className="status-cell">
        <div className="k">数据截至</div>
        <div className="v" style={{ fontSize: 14 }}>{maxDate || '—'}</div>
      </div>
      <div className="status-cell">
        <div className="k">Agent</div>
        <div className="v">{rows.length}</div>
      </div>
      <div className="status-cell">
        <div className="k">总权益</div>
        <div className="v">{fmtMoney(totalEquity, meta.currency)}</div>
      </div>
      <div className="status-cell">
        <div className="k">整体收益</div>
        <div className={`v ${overall >= 0 ? 'up' : 'down'}`}>{fmtPct(overall)}</div>
      </div>
      <div className="status-cell">
        <div className="k">最佳</div>
        <div className="v" style={{ fontSize: 14 }}>{best ? best.name : '—'}</div>
      </div>
      {benchLast && (
        <div className="status-cell">
          <div className="k">基准 {benchLabel}</div>
          <div className={`v ${benchChg != null && benchChg >= 0 ? 'up' : 'down'}`} style={{ fontSize: 14 }}>
            {fmtNum(benchLast.close, 1)} {benchChg != null && <span style={{ fontSize: 11 }}>({fmtPct(benchChg)})</span>}
          </div>
        </div>
      )}
    </div>
  );
}
