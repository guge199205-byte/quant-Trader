import { useMemo, useState } from 'react';
import { LogLine, PositionRecord, TradeRecord } from '../api/client';
import { logoOf } from './ModelCard';
import { fmtMoney } from '../utils/format';
import './ModelChat.css';

/** 一个决策回合：user prompt（开盘任务）+ assistant 总结（决策推理）+ 当日成交 */
interface Round {
  date: string | null;
  user: string;
  thought: string;
}

/** nof1.ai 风格模型对话：
 *  消息卡（模型色块 + 状态标签 + 日期 + 摘要 + click to expand）→ 展开后
 *  用户提示词 / 思考链 / 交易决策 三个折叠区块。
 *  交易决策 由当日成交（/trades）拼出决策卡，字段对齐 nof1 结构，
 *  我们没有的结构化字段（止损/止盈/置信度/杠杆等）显示 —。
 */
export default function ModelChat({
  logs,
  trades,
  positions,
  model,
  currency = '$',
}: {
  logs: LogLine[];
  trades: TradeRecord[];
  positions?: PositionRecord[];
  model: string;
  currency?: string;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [sections, setSections] = useState<Record<number, Set<string>>>({});

  /** user prompt 里提取日期：如 "Please analyze and update today's (2026-08-25) positions." */
  const extractDate = (content: string): string | null => {
    const m = content.match(/\((\d{4}-\d{2}-\d{2})\)/);
    return m ? m[1] : null;
  };

  /** 成交按日期索引（交易决策 匹配） */
  const tradesByDate = useMemo(() => {
    const map = new Map<string, TradeRecord[]>();
    for (const t of trades ?? []) {
      const arr = map.get(t.date) ?? [];
      arr.push(t);
      map.set(t.date, arr);
    }
    return map;
  }, [trades]);

  /** 该 symbol 在交易日前一天是否已持仓（IS ADD 判断） */
  const hadPositionBefore = (date: string, symbol: string): boolean => {
    const snaps = positions ?? [];
    for (let i = snaps.length - 1; i >= 0; i--) {
      if ((snaps[i].date ?? '') < date) {
        return Number(snaps[i].positions?.[symbol] ?? 0) > 0;
      }
    }
    return false;
  };

  /** 回合分组：user 消息开新回合，assistant 消息并入最近的回合 */
  const rounds: Round[] = useMemo(() => {
    const out: Round[] = [];
    for (const line of logs ?? []) {
      for (const msg of line.new_messages ?? []) {
        const content = (msg.content ?? '').trim();
        if (!content) continue;
        const role = msg.role ?? 'system';
        if (role === 'user' || role === 'human') {
          out.push({ date: extractDate(content), user: content, thought: '' });
        } else if (role === 'assistant' || role === 'ai') {
          if (out.length === 0) out.push({ date: null, user: '', thought: '' });
          out[out.length - 1].thought = content;
        }
        // tool 消息不进回合（tool 调用 JSON 已在成交里体现）
      }
    }
    return out.reverse(); // 最新在上
  }, [logs]);

  const toggle = (idx: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  const toggleSection = (idx: number, key: string) =>
    setSections((prev) => {
      const cur = new Set(prev[idx] ?? []);
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      return { ...prev, [idx]: cur };
    });

  if (!rounds.length) return <div className="empty-state">暂无决策对话</div>;

  return (
    <div className="mc-list">
      {rounds.map((r, i) => {
        const dayTrades = r.date ? tradesByDate.get(r.date) ?? [] : [];
        const isOpen = open.has(i);
        const sec = sections[i] ?? new Set<string>();
        const status = dayTrades.length ? 'DAILY DECISION' : r.thought ? 'NO TRADE' : 'PROMPT';
        const dateLabel = r.date ? r.date.slice(5) : '—';
        const summary = (r.thought || r.user).replace(/\s+/g, ' ').slice(0, 120);
        return (
          <div className={`mc-card ${isOpen ? 'open' : ''}`} key={i}>
            <div className="mc-head" onClick={() => toggle(i)} role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(i); } }}>
              <span className="mc-logo">{logoOf(model)}</span>
              <span className="mc-model">{model.replace('deepseek-v4-', '').toUpperCase()}</span>
              <span className="mc-status">{status}</span>
              <span className="mc-date">{dateLabel}</span>
              <span className={`mc-expand ${isOpen ? 'open' : ''}`}>{isOpen ? '▼' : '▶'}</span>
            </div>
            <div className="mc-summary">{summary}…</div>
            {isOpen && (
              <div className="mc-body">
                {/* 用户提示词 */}
                {r.user && (
                  <div className={`mc-section ${sec.has('prompt') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'prompt')}>
                      <span className="mc-caret">{sec.has('prompt') ? '▶' : '▼'}</span>
                      用户提示词
                    </div>
                    {!sec.has('prompt') && <pre className="mc-code">{r.user}</pre>}
                  </div>
                )}
                {/* 思考链 */}
                {r.thought && (
                  <div className={`mc-section ${sec.has('thought') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'thought')}>
                      <span className="mc-caret">{sec.has('thought') ? '▶' : '▼'}</span>
                      思考链
                    </div>
                    {!sec.has('thought') && <pre className="mc-code mc-thought">{r.thought}</pre>}
                  </div>
                )}
                {/* 交易决策 */}
                <div className={`mc-section ${sec.has('trades') ? 'folded' : ''}`}>
                  <div className="mc-section-head" onClick={() => toggleSection(i, 'trades')}>
                    <span className="mc-caret">{sec.has('trades') ? '▶' : '▼'}</span>
                    交易决策{dayTrades.length ? ` (${dayTrades.length})` : ''}
                  </div>
                  {!sec.has('trades') &&
                    (dayTrades.length ? (
                      <div className="mc-trades">
                        {dayTrades.map((t, j) => (
                          <DecisionCard
                            key={`${t.date}-${j}`}
                            trade={t}
                            isAdd={hadPositionBefore(t.date, t.symbol)}
                            currency={currency}
                            thought={r.thought}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="mc-no-trade">
                        {r.date ? `No trading decisions recorded on ${r.date}.` : 'No trading decisions recorded.'}
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** nof1 决策卡：字段网格对齐 nof1.ai 交易决策 布局。
 *  有数据的填真实值；我们没有的结构化字段（止损/止盈/置信度/杠杆/风控位）显示 —。 */
function DecisionCard({
  trade,
  isAdd,
  currency,
  thought,
}: {
  trade: TradeRecord;
  isAdd: boolean;
  currency: string;
  thought: string;
}) {
  const side = (trade.action ?? '').toLowerCase() === 'buy' ? 'buy' : 'sell';
  const fields: [string, string][] = [
    ['证券', `xyz:${trade.symbol}`],
    ['信号', side],
    ['数量', String(trade.amount)],
    ['加仓', String(isAdd)],
    ['成交后现金', fmtMoney(trade.cash_after ?? 0, currency)],
    ['杠杆', '—'],
    ['止损', '—'],
    ['止盈', '—'],
    ['置信度', '—'],
    ['风险金额', '—'],
    ['失效条件', '—'],
    ['决策理由', thought ? thought.replace(/\s+/g, ' ').slice(0, 160) + '…' : '—'],
  ];
  return (
    <div className="mc-decision">
      {fields.map(([k, v]) => (
        <div className="mc-field" key={k}>
          <span className="mc-key">{k}</span>
          <span className={`mc-val ${v === '—' ? 'na' : ''}`}>{v}</span>
        </div>
      ))}
    </div>
  );
}
