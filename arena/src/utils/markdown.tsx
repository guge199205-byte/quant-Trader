import { ReactNode } from 'react';

/**
 * 轻量行内 markdown 渲染（不引库）：
 *   `**粗体**` → <strong>，`` `代码` `` → <code>，`*斜体*` → <em>
 * 换行保留（配合 white-space: pre-wrap 的容器）。
 * 用于模型对话的 LLM 输出——原文里的 ** 不再裸奔。
 */
export function renderInline(text: string): ReactNode[] {
  const lines = text.split('\n');
  return lines.map((line, i) => (
    <span key={i}>
      {line.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g).map((part, j) => {
        if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
          return <strong key={j}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
          return (
            <code key={j} style={{ background: '#f0f0f0', border: '1px solid #ddd', padding: '0 3px', fontSize: '0.92em' }}>
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.startsWith('*') && part.endsWith('*') && !part.startsWith('**') && part.length > 2) {
          return <em key={j}>{part.slice(1, -1)}</em>;
        }
        return part;
      })}
      {i < lines.length - 1 ? '\n' : null}
    </span>
  ));
}
