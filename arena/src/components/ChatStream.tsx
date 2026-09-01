import { useMemo, useState } from 'react';
import { LogLine } from '../api/client';
import { logoOf, modelColor, shortName } from './ModelCard';
import { renderInline } from '../utils/markdown';
import './ModelChat.css';

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
}: {
  agents: { name: string; lines: LogLine[] }[];
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

  if (!rounds.length) return <div className="empty-state">暂无分析记录</div>;

  return (
    <div className="mc-list">
      {rounds.map((r, i) => {
        const isOpen = open.has(i);
        const sec = sections[i] ?? new Set<string>();
        const summary = (r.thought || r.user).replace(/\s+/g, ' ').trim();
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
                {r.thought && (
                  <div className={`mc-section ${sec.has('thought') ? 'folded' : ''}`}>
                    <div className="mc-section-head" onClick={() => toggleSection(i, 'thought')}>
                      <span className="mc-caret">{sec.has('thought') ? '▶' : '▼'}</span>
                      分析内容
                    </div>
                    {!sec.has('thought') && <pre className="mc-code mc-thought">{renderInline(r.thought)}</pre>}
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
