/** 策略排行：一个股票池里，每个策略在全池标的上的平均表现（跑赢买入持有的比例是核心列）。 */
import type { LabBatchDetail, LabBatchRank } from '../../api/client';

const fmt = (v: number | null, d = 1, suffix = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(d)}${suffix}`;

const tone = (v: number | null) => (v === null ? '' : v >= 0 ? 'up' : 'down');

/** 均值净收益条形：以全表最大绝对值为满格 */
function Bar({ v, max }: { v: number | null; max: number }) {
  if (v === null) return null;
  const w = Math.min(100, (Math.abs(v) / (max || 1)) * 100);
  return (
    <span className="lab-bar">
      <i className={v >= 0 ? 'pos' : 'neg'} style={{ width: `${w}%` }} />
    </span>
  );
}

export default function BatchRankTable({ detail }: { detail: LabBatchDetail }) {
  const rows = detail.ranking ?? [];
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.mean_net_pct ?? 0)));

  if (!rows.length) return <div className="lab-empty">该批次没有聚合结果。</div>;

  return (
    <div className="lab-batch-body">
      <div className="lab-table-wrap lab-table-tall">
        <table className="lab-rank">
          <thead>
            <tr>
              <th>#</th>
              <th>策略</th>
              <th className="num">标的数</th>
              <th className="num">均值净收益</th>
              <th className="num">中位数</th>
              <th className="num">均回撤</th>
              <th className="num">夏普</th>
              <th className="num">盈亏比</th>
              <th className="num">胜率</th>
              <th className="num">跑赢持有</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r: LabBatchRank, i) => (
              <tr key={r.strategy}>
                <td className="idx">{i + 1}</td>
                <td className="name">{r.name}</td>
                <td className="num">{r.symbols}</td>
                <td className={`num ${tone(r.mean_net_pct)}`}>
                  {fmt(r.mean_net_pct, 1, '%')}
                  <Bar v={r.mean_net_pct} max={max} />
                </td>
                <td className={`num ${tone(r.median_net_pct)}`}>{fmt(r.median_net_pct, 1, '%')}</td>
                <td className="num down">{fmt(r.mean_dd_pct, 1, '%')}</td>
                <td className="num">{fmt(r.mean_sharpe, 2)}</td>
                <td className="num">{fmt(r.mean_profit_factor, 2)}</td>
                <td className="num">{fmt(r.mean_win_rate_pct, 1, '%')}</td>
                <td className="num">{fmt(r.beat_bh_pct, 1, '%')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="lab-note">
        口径：{detail.adj === 'backward' ? '后复权' : detail.adj} · A股多头单向 · 本金 10 万 /
        95% 仓位 / 双边 0.05% 费率 / 1 tick 滑点 · 单标的 K 线不足 250 根不入统计。
        「跑赢持有」= 该策略跑赢买入持有的标的占比（长期牛市里趋势策略天然吃亏，看这个比看收益更实在）。
      </p>
    </div>
  );
}
