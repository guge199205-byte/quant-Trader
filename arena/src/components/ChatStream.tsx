import { useMemo, useState } from 'react';
import { LogLine } from '../api/client';
import { logoOf, modelColor, shortName } from './ModelCard';
import { renderInline } from '../utils/markdown';
import './ModelChat.css';
import { modeOf } from '../utils/modeTag';
import { FillLike, renderActionTags } from '../utils/actionTags';
import { parseAnalysis } from '../utils/parseAnalysis';

/** 一个分析回合：单条日志（一次 LLM 分析 = user prompt + assistant 总结） */
interface MixedRound {
  model: string;
  /** 日志写入时间（实盘分析时间），用于全局倒序 */
  ts: string | null;
  user: string;
  thought: string;
}

/** 模型对话 — 全部模型混合流：
 *  不按模型分组，所有模型的最新分析按时间全局倒序混排（每个模型的最新都排前面），
 *  卡片边框按模型配色区分。 */
export default function ChatStream({
  agents,
  fills,
  heldCodes,
}: {
  agents: { name: string; lines: LogLine[] }[];
  /** 实盘成交事实（时间窗匹配 → 动作标签以成交为准，不靠文字猜） */
  fills?: FillLike[];
  /** 当前仍持有的代码集合（卖后仍持=减仓，卖光=清仓；买后已持=加仓） */
  heldCodes?: Set<string>;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [sections, setSections] = useState<Record<number, Set<string>>>({});

  /** 跨行合并成回合：user 开新回合，assistant 并入最近回合。
   *  A 股日志单行含 user+assistant；港股把 user/assistant 拆到不同日志行（各 1 条），
   *  必须跨行合并才能还原完整回合，否则 assistant-only 行「没有用户提示词」。
   *  港股日志缺 timestamp 字段，回退从 user prompt 文本里抽日期（today's (2026-08-28)）。 */
  const rounds: MixedRound[] = useMemo(() => {
    const extractDate = (s: string): string | null => {
      const m = s.match(/[（(](\d{4}-\d{2}-\d{2})[）)]/);
      return m ? m[1] : null;
    };
    const out: MixedRound[] = [];
    for (const ag of agents) {
      let cur: MixedRound | null = null;
      const flush = () => {
        if (cur && (cur.user || cur.thought)) out.push(cur);
        cur = null;
      };
      for (const line of ag.lines) {
        const lineTs = line.timestamp ?? null;
        for (const msg of line.new_messages ?? []) {
          const content = (msg.content ?? '').trim();
          if (!content) continue;
          const role = msg.role ?? 'system';
          if (role === 'user' || role === 'human') {
            flush();
            cur = { model: ag.name, ts: lineTs ?? extractDate(content), user: content, thought: '' };
          } else if (role === 'assistant' || role === 'ai') {
            if (!cur) cur = { model: ag.name, ts: lineTs, user: '', thought: '' };
            cur.thought += (cur.thought ? '\n\n' : '') + content;
            if (!cur.ts) cur.ts = lineTs;
          }
        }
      }
      flush();
    }
    // 最新分析在前（跨模型全局排序）
    return out.sort((a, b) => {
      if (a.ts && b.ts) return a.ts < b.ts ? 1 : -1;
      if (a.ts) return -1;
      if (b.ts) return 1;
      return 0;
    });
  }, [agents]);

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

  /** 四段式解析（总结/链路/决策/推理），与 rounds 对齐 */
  const parsed = useMemo(() => rounds.map((r) => parseAnalysis(r.thought)), [rounds]);

  if (!rounds.length) return <div className="empty-state">暂无分析记录</div>;

  return (
    <div className="mc-list">
      {rounds.map((r, i) => {
        const isOpen = open.has(i);
        const sec = sections[i] ?? new Set<string>();
        const pa = parsed[i];
        const summary = pa.summary || (r.thought || r.user).replace(/\s+/g, ' ').trim();
        return (
          <div
            className={`mc-card ${isOpen ? 'open' : ''}`}
            key={`${r.model}-${r.ts ?? i}`}
            style={{ borderColor: modelColor(r.model) }}
          >
            <div
              className="mc-head"
              onClick={() => toggle(i)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggle(i);
                }
              }}
            >
              <span className="mc-logo">{logoOf(r.model)}</span>
              <span className="mc-model" style={{ color: modelColor(r.model) }}>
                {shortName(r.model)}
              </span>
              {modeOf(r.user)}
              {renderActionTags(r.thought, {
                fills,
                model: r.model,
                tsMs: r.ts ? new Date(r.ts).getTime() : null,
                heldCodes,
              })}
              <span className="mc-status">{r.thought ? '已分析' : '仅提示'}</span>
              <span className="mc-date">{r.ts ? r.ts.slice(5, 16) : '—'}</span>
              <span className={`mc-expand ${isOpen ? 'open' : ''}`}>{isOpen ? '▼' : '▶'}</span>
            </div>
            <div className="mc-summary">{renderInline(summary)}</div>
            {isOpen && (
              <div className="mc-body">
                {r.user && (
                  <div className={`mc-section ${sec.has('prompt') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'prompt')}>
                      <span className="mc-caret">{sec.has('prompt') ? '▶' : '▼'}</span>
                      用户提示词
                    </div>
                    {!sec.has('prompt') && <pre className="mc-code">{renderInline(r.user)}</pre>}
                  </div>
                )}
                {pa.chain && (
                  <div className={`mc-section ${sec.has('chain') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'chain')}>
                      <span className="mc-caret">{sec.has('chain') ? '▶' : '▼'}</span>
                      分析链路（工具调用）
                    </div>
                    {!sec.has('chain') && <pre className="mc-code">{renderInline(pa.chain)}</pre>}
                  </div>
                )}
                {pa.decisions.length > 0 && (
                  <div className={`mc-section ${sec.has('decisions') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'decisions')}>
                      <span className="mc-caret">{sec.has('decisions') ? '▶' : '▼'}</span>
                      交易决策{pa.decisions.length ? ` (${pa.decisions.length})` : ''}
                    </div>
                    {!sec.has('decisions') && (
                      <div className="mc-trades">
                        {pa.decisions.slice(0, 6).map((d, k) => {
                          const act = String(d.action || '').toLowerCase();
                          const cls = act === 'buy' ? 'buy' : act === 'sell' ? 'sell' : 'hold';
                          const tag =
                            act === 'buy' ? '买入' : act === 'sell' ? '卖出' : act === 'watch' ? '观察' : '持有';
                          const exitBits = [
                            d.stop_loss != null ? `止损 ${d.stop_loss}` : '',
                            d.take_profit != null ? `止盈 ${d.take_profit}` : '',
                            d.move_stop != null ? `移动止损 ${d.move_stop}` : '',
                            d.confidence ? `置信 ${Math.round(d.confidence * 100)}%` : '',
                            d.risk_amount != null ? `风险 ¥${d.risk_amount.toLocaleString('en-US')}` : '',
                          ].filter(Boolean);
                          return (
                            <div className="mc-decision-mini" key={`${d.code}-${k}`}>
                              <div className="mc-decision-mini-row">
                                <span className={`mc-decision-mini-side ${cls}`}>{tag}</span>
                                <b className="mc-decision-mini-code">{d.name || d.code || '—'}</b>
                                {d.pct != null && (
                                  <span className="mc-decision-mini-pct">{Math.round(d.pct * 100)}%</span>
                                )}
                                <span className="mc-decision-mini-reason">{d.reason ?? ''}</span>
                              </div>
                              {(exitBits.length > 0 || d.invalidation) && (
                                <div className="mc-decision-mini-exit">
                                  {exitBits.join(' · ')}
                                  {d.invalidation ? ` · 失效: ${d.invalidation}` : ''}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
                {pa.reasoning && (
                  <div className={`mc-section ${sec.has('reason') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'reason')}>
                      <span className="mc-caret">{sec.has('reason') ? '▶' : '▼'}</span>
                      推理论证
                    </div>
                    {!sec.has('reason') && <pre className="mc-code mc-thought">{renderInline(pa.reasoning)}</pre>}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
