import { useMemo, useState } from 'react';
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
import { logoOf } from '../components/ModelCard';
import { fmtDate, fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Models.css';

/** filter(Boolean) 类型守卫 */
const nonNull = <T,>(list: (T | null)[]): T[] => list.filter((x): x is T => x !== null);

/** 模型页 —— 参考 nof0 models.html：统计条 + 模型卡片网格（coke 视觉） */
export default function Models() {
  const nav = useNavigate();
  const [filter, setFilter] = useState<MarketId | 'all'>('all');

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

  // 统计条
  const stats = useMemo(() => {
    if (!cards.length) return { count: 0, equity: null, avg: null, win: 0 };
    const equity = cards.reduce((s, c) => s + (c.perf.summary.end_equity ?? 0), 0);
    const avg = cards.reduce((s, c) => s + (c.perf.summary.total_return ?? 0), 0) / cards.length;
    const win = cards.filter((c) => (c.perf.summary.total_return ?? 0) > 0).length;
    return { count: cards.length, equity, avg, win };
  }, [cards]);

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}</div>;
  }

  return (
    <div className="page">
      <div className="models-header">
        <h1 className="models-title">模型</h1>
        <div className="market-chips">
          {([
            ['all', '全部'],
            ['us', '美股'],
            ['cn', 'A股'],
            ['hk', '港股'],
          ] as const).map(([f, label]) => (
            <button
              key={f}
              className={`chip ${f} ${filter === f ? 'active' : ''}`}
              style={{ borderRadius: 0 }}
              onClick={() => setFilter(f as MarketId | 'all')}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="models-stats">
        <div className="stat-item">
          <div className="stat-label">模型数量</div>
          <div className="stat-value">{stats.count}</div>
          <div className="stat-sub">已产生交易数据的 Agent</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">总权益</div>
          <div className="stat-value">{stats.equity != null ? fmtMoney(stats.equity, '$') : '—'}</div>
          <div className="stat-sub">全部模型合计</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">平均收益率</div>
          <div className={`stat-value ${pnlClass(stats.avg)}`}>{fmtPct(stats.avg)}</div>
          <div className="stat-sub">全体模型均值</div>
        </div>
        <div className="stat-item">
          <div className="stat-label">✅ 盈利模型</div>
          <div className="stat-value">{stats.win}/{stats.count || '—'}</div>
          <div className="stat-sub">
            {stats.count ? `胜率 ${Math.round((stats.win / stats.count) * 100)}%` : ''}
          </div>
        </div>
      </div>

      {overview.loading && !cards.length ? (
        <div className="loading"><div className="spinner" />加载中…</div>
      ) : (
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
      )}
    </div>
  );
}
