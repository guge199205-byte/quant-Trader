import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MarketId,
  Performance,
  PositionRecord,
  fetchOverview,
  fetchPerformance,
  fetchPositions,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { logoOf } from './ModelCard';
import { fmtDate, fmtMoney, fmtPct, pnlClass } from '../utils/format';
import '../pages/Models.css';

/** filter(Boolean) 类型守卫 */
const nonNull = <T,>(list: (T | null)[]): T[] => list.filter((x): x is T => x !== null);

/** 模型卡片网格（原"模型"页提炼，供"模型排行榜"融合页复用）：
 *  每模型一张卡（logo/名称/市场/权益/核心指标/持仓 chips），点击进详情。
 *  数据自拉（overview 名单 → perfs + positions），60s 轮询。 */
export default function ModelCards({ filter }: { filter: MarketId | 'all' }) {
  const nav = useNavigate();

  const overview = usePolling(() => fetchOverview(), [], 60000);

  // 全部 agent 名单（含 market）
  const agentList = useMemo(() => {
    const out: { market: MarketId; agent: string; hasSummary: boolean }[] = [];
    const ov = overview.data;
    if (!ov) return out;
    for (const m of ['cn', 'hk', 'us'] as MarketId[]) {
      for (const r of ov.markets[m] ?? []) {
        out.push({ market: m, agent: r.name, hasSummary: !!r.summary });
      }
    }
    return out;
  }, [overview.data]);

  const listKey = agentList.map((a) => `${a.market}:${a.agent}`).join('|');

  // 每 agent 的 performance + positions（仅拉有 summary 的，60s）
  const perfs = usePolling(
    () =>
      Promise.all(
        agentList.filter((a) => a.hasSummary).map((a) =>
          fetchPerformance(a.agent, a.market)
            .then((p) => ({ ...a, perf: p }))
            .catch(() => null),
        ),
      ).then((l) => nonNull(l) as { market: MarketId; agent: string; perf: Performance }[]),
    [listKey],
    60000,
  );

  const positions = usePolling(
    () =>
      Promise.all(
        agentList.filter((a) => a.hasSummary).map((a) =>
          fetchPositions(a.agent, a.market)
            .then((p) => ({ agent: a.agent, market: a.market, recs: p }))
            .catch(() => null),
        ),
      ).then((l) => nonNull(l) as { agent: string; market: MarketId; recs: PositionRecord[] }[]),
    [listKey],
    60000,
  );

  // 组装卡片
  const cards = useMemo(() => {
    const posByAgent = new Map((positions.data ?? []).map((p) => [p.agent, p]));
    return (perfs.data ?? []).map((c) => ({
      market: c.market,
      agent: c.agent,
      perf: c.perf,
      positions: posByAgent.get(c.agent)?.recs ?? null,
    }));
  }, [perfs.data, positions.data]);

  const visible = filter === 'all' ? cards : cards.filter((c) => c.market === filter);
  const sorted = [...visible].sort(
    (a, b) => (b.perf.summary.total_return ?? -Infinity) - (a.perf.summary.total_return ?? -Infinity),
  );

  return (
    <div className="models-grid">
      {sorted.map((c) => {
        const s = c.perf.summary;
        const meta = marketMeta(c.market);
        const wins = (s.total_return ?? 0) >= 0;
        const last = c.perf.points[c.perf.points.length - 1];
        const posRec = c.positions?.[c.positions.length - 1];
        const holdings = posRec
          ? Object.entries(posRec.positions ?? {}).filter(([sym]) => sym !== 'CASH')
          : [];
        return (
          <div
            className="model-card-lg"
            key={`${c.market}:${c.agent}`}
            style={{ ['--card-accent' as string]: wins ? '#10a37f' : '#ef4444' }}
            onClick={() => nav(`/model/${c.market}/${encodeURIComponent(c.agent)}`)}
          >
            <div className="md-head">
              <span className="md-logo">{logoOf(c.agent)}</span>
              <span className="md-name">{c.agent.replace('deepseek-v4-', 'DeepSeek V4 ')}</span>
              <span className="md-market">{meta.label}</span>
              <span className={`md-badge ${wins ? 'win' : 'loss'}`}>{wins ? '盈利' : '亏损'}</span>
            </div>
            <div className="md-equity">
              {fmtMoney(s.end_equity, meta.currency)}{' '}
              <small className={pnlClass(s.total_return)}>
                {wins ? '+' : ''}{fmtPct(s.total_return, 2, false)}
              </small>
            </div>
            <div className="md-metrics">
              <div className="m"><span className="k">初始资金</span><span className="v">{fmtMoney(s.start_equity, meta.currency)}</span></div>
              <div className="m"><span className="k">最大回撤</span><span className="v down">{fmtPct(s.max_drawdown, 2, false)}</span></div>
              <div className="m"><span className="k">成交次数</span><span className="v">{s.closed_trades ?? 0}</span></div>
              <div className="m"><span className="k">持仓市值</span><span className="v">{fmtMoney(last?.market_value ?? 0, meta.currency)}</span></div>
            </div>
            {holdings.length > 0 && (
              <div className="md-holdings">
                {holdings.slice(0, 3).map(([sym, qty]) => (
                  <span key={sym} className="holding-chip">{sym} ×{Number(qty).toLocaleString('en-US')}</span>
                ))}
                {holdings.length > 3 && <span className="holding-more">+{holdings.length - 3}</span>}
              </div>
            )}
            <div className="md-status">
              <span>现金 {fmtMoney(c.perf.points[c.perf.points.length - 1]?.cash ?? 0, meta.currency)}</span>
              <span>截至 {fmtDate(agentList.find((a) => a.agent === c.agent) ? (overview.data?.markets[c.market]?.find((r) => r.name === c.agent)?.latest_date ?? null) : null)}</span>
            </div>
          </div>
        );
      })}
      {!sorted.length && <div className="empty-state" style={{ gridColumn: '1 / -1' }}>暂无模型数据</div>}
    </div>
  );
}
