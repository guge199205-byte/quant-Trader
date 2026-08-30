import { useMemo, useState } from 'react';
import { LogLine } from '../api/client';

const ROLE_LABEL: Record<string, string> = {
  system: 'SYS',
  human: 'USER',
  ai: 'AI',
  tool: 'TOOL',
};

/** 决策日志面板：把 {signature, new_messages[]} 拍平成单条消息流。
 *  tool 消息含完整调用 JSON，默认折叠，点击展开。 */
export default function DecisionLog({ logs, limit = 80 }: { logs: LogLine[]; limit?: number }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const flat = useMemo(() => {
    const out: { idx: number; role: string; content: string }[] = [];
    let idx = 0;
    for (const line of logs) {
      for (const msg of line.new_messages ?? []) {
        const role = msg.role ?? 'system';
        const content = msg.content ?? '';
        if (!content) continue;
        out.push({ idx: idx++, role, content });
      }
    }
    return out.slice(-limit);
  }, [logs, limit]);

  const toggle = (idx: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });

  if (!flat.length) return <div className="loading">暂无决策日志</div>;

  return (
    <div>
      {flat.map((e) => {
        const isTool = e.role === 'tool';
        const open = expanded.has(e.idx);
        const display = isTool && !open ? `${e.content.slice(0, 160)}…` : e.content;
        return (
          <div key={e.idx} className="log-entry">
            <div className="log-head">
              <span className={`log-role ${e.role}`}>{ROLE_LABEL[e.role] ?? e.role.toUpperCase()}</span>
              {isTool && (
                <button className="btn" style={{ padding: '1px 8px', fontSize: 10 }} onClick={() => toggle(e.idx)}>
                  {open ? '折叠' : '展开'}
                </button>
              )}
            </div>
            <div className="log-content">{display}</div>
          </div>
        );
      })}
    </div>
  );
}
