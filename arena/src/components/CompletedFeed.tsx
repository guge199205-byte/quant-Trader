import { useMemo } from 'react';
import {
  ClosedTradeDetail,
  FutuClosedRow,
  MarketId,
  LiveTradeLog,
  fetchFutuClosed,
  fetchFutuTrades,
  fetchTradeDetail,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { logoOf, shortName } from './ModelCard';
import './CompletedFeed.css';

interface Props {
  agents: string[]; // 当前市场全部 agent（外部可按模型筛选后传入）
  market: MarketId;
  currency: string;
  stockNames?: Record<string, string>; // symbol → 中文名（缺失时回退显示代码）
  onCount?: (n: number) => void; // 平仓消息条数回调（供父级 filter-bar 计数）
}

interface AgentTrades {
  agent: string;
  trades: ClosedTradeDetail[];
}

interface FeedItem extends ClosedTradeDetail {
  agent: string;
}

/** 港股「已完成」feed 项：富途已平仓行 + 卖出成交（具体的平仓执行） */
interface HkFeedItem {
  code: string;
  name: string;
  qty: number; // 卖出数量（来自 SELL 订单；已平仓行无数量 → 0）
  entry_price: number; // 成本（来自已平仓行）
  exit_price: number; // 平仓价（SELL 成交均价 / 已平仓 nominal_price）
  realized_pl: number;
  exit_date: string; // SELL 成交时间；已平仓行无 → ''
  kind: 'closed' | 'sell'; // 已平仓持仓 / 卖出成交
}

/** 持仓时长：≥1 天按天、<1 天按小时（与 LastTradesTable 一致） */
const holdText = (days: number | null): string => {
  if (days == null) return '—';
  if (days < 1) return `${Math.round(days * 24)}H 0M`;
  return `${days}D 0H`;
};

const fmtQty = (v: number): string => v.toLocaleString('en-US', { maximumFractionDigits: 2 });

/** COMPLETED —— nof1 风格"completed a trade"平仓消息流（当前市场全部模型，最新在前）。 */
export default function CompletedFeed({ agents, market, currency, stockNames = {}, onCount }: Props) {
  const isHk = market === 'hk';
  const key = agents.join('|');

  // cn/us：按 agent 拉 FIFO 平仓明细（replay）
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

  // hk：富途已平仓行 + 卖出成交（一次轮询拉两路，按 code 关联实现盈亏）
  const hkFeed = usePolling(
    async (): Promise<HkFeedItem[]> => {
      const [closed, sells] = await Promise.all([
        fetchFutuClosed('SIMULATE').catch(() => [] as FutuClosedRow[]),
        fetchFutuTrades('SIMULATE')
          .then((ts) => ts.filter((t) => String(t.side).toUpperCase() === 'SELL' && Number(t.volume) > 0))
          .catch(() => [] as LiveTradeLog[]),
      ]);
      const plByCode = new Map<string, number>();
      const entryByCode = new Map<string, number>();
      const items: HkFeedItem[] = [];
      for (const c of closed) {
        plByCode.set(c.code, Number(c.realized_pl) || 0);
        entryByCode.set(c.code, Number(c.cost_price) || 0);
        items.push({
          code: c.code,
          name: c.name || c.code,
          qty: 0,
          entry_price: Number(c.cost_price) || 0,
          exit_price: Number(c.last_price) || 0,
          realized_pl: Number(c.realized_pl) || 0,
          exit_date: '',
          kind: 'closed',
        });
      }
      for (const s of sells) {
        items.push({
          code: s.code,
          name: s.name || s.code,
          qty: Number(s.volume) || 0,
          entry_price: entryByCode.get(s.code) ?? 0,
          exit_price: Number(s.price) || 0,
          realized_pl: plByCode.get(s.code) ?? 0,
          exit_date: s.ts,
          kind: 'sell',
        });
      }
      return items.sort((a, b) => (a.exit_date < b.exit_date ? 1 : -1));
    },
    [isHk],
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

  const hkItems = hkFeed.data ?? [];
  const count = isHk ? hkItems.length : items.length;
  onCount?.(count);

  if (!agents.length && !isHk) return <div className="empty-state">该市场暂无 Agent</div>;

  if (isHk) {
    if (hkFeed.loading && !hkItems.length) {
      return <div className="loading"><div className="spinner" />加载中…</div>;
    }
    if (hkFeed.error && !hkItems.length) {
      return <div className="empty-state">加载失败：{hkFeed.error}</div>;
    }
    if (!hkItems.length) {
      return (
        <div className="empty-state">暂无已平仓交易（富途模拟账户：无完全平仓持仓或卖出成交）</div>
      );
    }
    return (
      <div className="completed-feed">
        {hkItems.map((t, i) => {
          const pnl = t.realized_pl;
          return (
            <div className="feed-card" key={`${t.code}-${t.exit_date}-${i}`}>
              <div className="feed-head">
                <span className="feed-logo">🐂</span>
                <span className="feed-msg">
                  <b className="feed-agent">富途</b> 在{' '}
                  <b className="feed-symbol">
                    {t.name}
                    <span className="feed-code">{t.code}</span>
                  </b>{' '}
                  {t.kind === 'closed' ? '完成了一笔已平仓交易！' : '完成了一笔卖出成交！'}
                </span>
                <span className="feed-date">{t.exit_date ? t.exit_date.slice(5, 16) : '—'}</span>
              </div>
              <div className="feed-price">
                成本: {currency}{t.entry_price.toLocaleString('en-US', { maximumFractionDigits: 2 })}
                <span className="feed-arrow"> → </span>
                平仓: {currency}{t.exit_price.toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </div>
              <div className="feed-grid">
                <div className="feed-cell">
                  <span className="feed-k">数量</span>
                  <span className="feed-v">{t.qty > 0 ? fmtQty(t.qty) : '—'}</span>
                </div>
                <div className="feed-cell">
                  <span className="feed-k">成交金额</span>
                  <span className="feed-v">
                    {t.qty > 0 ? `${currency}${(t.qty * t.exit_price).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
                  </span>
                </div>
                <div className="feed-cell">
                  <span className="feed-k">类型</span>
                  <span className="feed-v">{t.kind === 'closed' ? '已平仓持仓' : '卖出成交'}</span>
                </div>
              </div>
              <div className={`feed-pnl ${pnl >= 0 ? 'up' : 'down'}`}>
                实现盈亏: {pnl >= 0 ? '+' : '-'}{currency}{Math.abs(pnl).toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

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
                <b className="feed-symbol">
                  {stockNames[t.symbol] ?? t.symbol}
                  <span className="feed-code">{t.symbol}</span>
                </b>{' '}
                完成了一笔交易！
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
