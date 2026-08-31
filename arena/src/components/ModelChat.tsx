import { useMemo, useState } from 'react';
import { LogLine, PositionRecord, TradeRecord } from '../api/client';
import { logoOf, modelColor } from './ModelCard';
import { fmtMoney } from '../utils/format';
import { renderInline, renderMarkdown } from '../utils/markdown';
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
  names = {},
}: {
  logs: LogLine[];
  trades: TradeRecord[];
  positions?: PositionRecord[];
  model: string;
  currency?: string;
  names?: Record<string, string>;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [sections, setSections] = useState<Record<number, Set<string>>>({});

  /** user prompt 里提取日期：英文 "today's (2026-08-25) positions." 或中文 "今日（2026-08-25）的持仓"。 */
  const extractDate = (content: string): string | null => {
    const m = content.match(/[（(](\d{4}-\d{2}-\d{2})[）)]/);
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
        // 只算真实成交（buy/sell）——no_trade 记录不进决策卡
        const dayTrades = r.date
          ? (tradesByDate.get(r.date) ?? []).filter((t) => t.action === 'buy' || t.action === 'sell')
          : [];
        const isOpen = open.has(i);
        const sec = sections[i] ?? new Set<string>();
        const status = dayTrades.length ? 'DAILY DECISION' : r.thought ? 'NO TRADE' : 'PROMPT';
        const dateLabel = r.date ? r.date.slice(5) : '—';
        // 第一行摘要：取思考链/提示词的首个非空行（通常是"盘中持仓分析（…）"或
        // "### 标题"），去掉 markdown 符号，而不是整段原文截断
        const firstLine =
          (r.thought || r.user)
            .split('\n')
            .map((l) => l.trim())
            .find((l) => l.length > 0) ?? '';
        const cleanFirst = firstLine.replace(/^#{1,6}\s*/, '').replace(/\*\*/g, '');
        const summary = cleanFirst.length > 60 ? cleanFirst.slice(0, 60) + '…' : cleanFirst;
        return (
          <div className={`mc-card ${isOpen ? 'open' : ''}`} key={i}
            style={{ borderColor: modelColor(model) }}>
            <div className="mc-head" onClick={() => toggle(i)} role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(i); } }}>
              <span className="mc-logo">{logoOf(model)}</span>
              <span className="mc-model" style={{ color: modelColor(model) }}>
                {model.replace('deepseek-v4-', '').toUpperCase()}
              </span>
              <span className="mc-status">{status}</span>
              <span className="mc-date">{dateLabel}</span>
              <span className={`mc-expand ${isOpen ? 'open' : ''}`}>{isOpen ? '▼' : '▶'}</span>
            </div>
            <div className="mc-summary">{renderInline(summary)}…</div>
            {isOpen && (
              <div className="mc-body">
                {/* 用户提示词 */}
                {r.user && (
                  <div className={`mc-section ${sec.has('prompt') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'prompt')}>
                      <span className="mc-caret">{sec.has('prompt') ? '▶' : '▼'}</span>
                      用户提示词
                    </div>
                    {!sec.has('prompt') && <div className="mc-code">{renderMarkdown(r.user)}</div>}
                  </div>
                )}
                {/* 思考链 */}
                {r.thought && (
                  <div className={`mc-section ${sec.has('thought') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'thought')}>
                      <span className="mc-caret">{sec.has('thought') ? '▶' : '▼'}</span>
                      思考链
                    </div>
                    {!sec.has('thought') && <div className="mc-code mc-thought">{renderMarkdown(r.thought)}</div>}
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
                            names={names}
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

/** 决策卡：证券/方向/数量/加仓/成交后现金 + 决策理由（全宽）。
 *  只显示真实存在的字段 —— 我们没有结构化数据（止损/止盈/置信度等）就不占位，
 *  证券显示中文名 + 代码，买入/卖出带色。 */
function DecisionCard({
  trade,
  isAdd,
  currency,
  thought,
  names,
}: {
  trade: TradeRecord;
  isAdd: boolean;
  currency: string;
  thought: string;
  names: Record<string, string>;
}) {
  const side = (trade.action ?? '').toLowerCase() === 'buy' ? 'buy' : 'sell';
  const name = names[trade.symbol];
  // 决策理由：去掉 ### 前缀/剥掉裸 **（renderInline 处理），截断到 160 字
  const reason = thought ? thought.replace(/\s+/g, ' ').slice(0, 160) + '…' : '—';
  const reasonNodes = reason === '—' ? reason : renderInline(reason);
  return (
    <div className="mc-decision">
      <div className="mc-field">
        <span className="mc-key">证券</span>
        <span className="mc-val">
          {name ? (
            <span className="mc-sym-name">
              {name} <span className="mc-sym-code">{trade.symbol}</span>
            </span>
          ) : (
            trade.symbol
          )}
        </span>
      </div>
      <div className="mc-field">
        <span className="mc-key">信号</span>
        <span className={`mc-val mc-side ${side}`}>{side === 'buy' ? '买入 BUY' : '卖出 SELL'}</span>
      </div>
      <div className="mc-field">
        <span className="mc-key">数量</span>
        <span className="mc-val">{Number(trade.amount).toLocaleString('en-US')}</span>
      </div>
      <div className="mc-field">
        <span className="mc-key">加仓</span>
        <span className="mc-val">{isAdd ? '是' : '否'}</span>
      </div>
      <div className="mc-field mc-field-wide">
        <span className="mc-key">成交后现金</span>
        <span className="mc-val">{fmtMoney(trade.cash_after ?? 0, currency)}</span>
      </div>
      <div className="mc-field mc-field-wide">
        <span className="mc-key">决策理由</span>
        <span className={`mc-val mc-reason ${reason === '—' ? 'na' : ''}`}>{reasonNodes}</span>
      </div>
    </div>
  );
}
