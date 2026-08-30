import { useMemo } from 'react';
import { ClosedTradeDetail, MarketId, fetchTradeDetail } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { logoOf, shortName } from './ModelCard';
import './CompletedFeed.css';

interface Props {
  agents: string[]; // 当前市场全部 agent
  market: MarketId;
  currency: string;
}

interface AgentTrades {
  agent: string;
  trades: ClosedTradeDetail[];
}

interface FeedItem extends ClosedTradeDetail {
  agent: string;
}

/** 持仓时长：≥1 天按天、<1 天按小时（与 LastTradesTable 一致） */
const holdText = (days: number | null): string => {
  if (days == null) return '—';
  if (days < 1) return `${Math.round(days * 24)}H 0M`;
  return `${days}D 0H`;
};

const fmtQty = (v: number): string => v.toLocaleString('en-US', { maximumFractionDigits: 2 });

/** COMPLETED —— nof1 风格"completed a trade"平仓消息流（当前市场全部模型，最新在前）。 */
export default function CompletedFeed({ agents, market, currency }: Props) {
  const key = agents.join('|');
  const feeds = usePolling(
    () =>
      Promise.all(
        agents.map(
          async (a): Promise<AgentTrades> => ({
            agent: a,
            trades: await fetchTradeDetail(a, market, 25).catch(() => [] as ClosedTradeDetail[]),
          }),
        ),
      ),
    [key, market],
    30000,
  );

  const items: FeedItem[] = useMemo(() => {
    const out: FeedItem[] = [];
    for (const f of feeds.data ?? []) {
      for (const t of f.trades) out.push({ ...t, agent: f.agent });
    }
    out.sort((a, b) => (a.exit_date < b.exit_date ? 1 : -1));
    return out;
  }, [feeds.data]);

  if (!agents.length) return <div className="empty-state">该市场暂无 Agent</div>;
  if (feeds.loading && !items.length) {
    return <div className="loading"><div className="spinner" />加载中…</div>;
  }
  if (feeds.error && !items.length) {
    return <div className="empty-state">加载失败：{feeds.error}</div>;
  }
  if (!items.length) return <div className="empty-state">暂无已平仓交易</div>;

  return (
    <div className="completed-feed">
      {items.map((t, i) => {
        const pnl = t.pnl;
        const entryNotional = t.qty * t.entry_price;
        return (
          <div className="feed-card" key={`${t.agent}-${t.exit_date}-${t.symbol}-${i}`}>
            <div className="feed-head">
              <span className="feed-logo">{logoOf(t.agent)}</span>
              <span className="feed-msg">
                <b className="feed-agent">{shortName(t.agent)}</b> 在{' '}
                <b className="feed-symbol">{t.symbol}</b> 完成了一笔交易！
              </span>
              <span className="feed-date">{t.exit_date.slice(5)}</span>
            </div>
            <div className="feed-price">
              价格: {currency}{t.entry_price.toLocaleString('en-US', { maximumFractionDigits: 2 })}
              <span className="feed-arrow"> → </span>
              {currency}{t.exit_price.toLocaleString('en-US', { maximumFractionDigits: 2 })}
            </div>
            <div className="feed-grid">
              <div className="feed-cell">
                <span className="feed-k">数量</span>
                <span className="feed-v">{fmtQty(t.qty)}</span>
              </div>
              <div className="feed-cell">
                <span className="feed-k">名义金额</span>
                <span className="feed-v">
                  {currency}{entryNotional.toLocaleString('en-US', { maximumFractionDigits: 0 })} →{' '}
                  {currency}{t.notional.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                </span>
              </div>
              <div className="feed-cell">
                <span className="feed-k">持仓时长</span>
                <span className="feed-v">{holdText(t.hold_days)}</span>
              </div>
            </div>
            <div className={`feed-pnl ${pnl >= 0 ? 'up' : 'down'}`}>
              净盈亏: {pnl >= 0 ? '+' : '-'}{currency}{Math.abs(pnl).toLocaleString('en-US', { maximumFractionDigits: 2 })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
