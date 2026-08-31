import { ReactNode } from 'react';

/**
 * 轻量行内 markdown 渲染（不引库）：
 *   `**粗体**` → <strong>，`` `代码` `` → <code>，`*斜体*` → <em>
 *   `### 标题`（1~6 级）→ 去掉 # 前缀渲染为加粗（模型输出里的 ### 不再裸奔）
 * 换行保留（配合 white-space: pre-wrap 的容器）。
 */
function renderParts(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g).map((part, j) => {
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
  });
}

export function renderInline(text: string): ReactNode[] {
  const lines = text.split('\n');
  return lines.map((line, i) => {
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    const body = heading ? heading[1] : line;
    return (
      <span key={i}>
        {heading ? <strong>{renderParts(body)}</strong> : renderParts(body)}
        {i < lines.length - 1 ? '\n' : null}
      </span>
    );
  });
}
