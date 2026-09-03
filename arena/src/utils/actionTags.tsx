import type { ReactNode } from 'react';

/** 分析回合的动作标签：从决策文本提取本轮实际动作（空仓/持仓/减仓/
 *  加仓/买入/卖出/清仓/新增股票），跟在模型名/模式标签后面。
 *  卖系优先：清仓 > 减仓 > 卖出；买系：加仓 > 新增 > 买入；
 *  无动作 → 持仓；空仓 → 空仓（灰虚线风格）。最多 3 个。 */
export interface ActionTag {
  label: string;
  cls: string;
}

export function actionTagsOf(content?: string | null): ActionTag[] {
  const t = content ?? '';
  const tags: ActionTag[] = [];
  if (/清仓/.test(t)) tags.push({ label: '清仓', cls: 'sell' });
  else if (/减仓/.test(t)) tags.push({ label: '减仓', cls: 'sell' });
  else if (/卖出/.test(t)) tags.push({ label: '卖出', cls: 'sell' });

  if (/加仓/.test(t)) tags.push({ label: '加仓', cls: 'buy' });
  else if (/新增|建仓|新买入/.test(t)) tags.push({ label: '新增股票', cls: 'buy' });
  else if (/买入/.test(t)) tags.push({ label: '买入', cls: 'buy' });

  if (!tags.length) {
    if (/空仓|候选池复盘/.test(t)) tags.push({ label: '空仓', cls: 'empty' });
    else tags.push({ label: '持仓', cls: 'hold' });
  }
  return tags.slice(0, 3);
}

/** 渲染为标签组（ReactNode），供对话卡头部直接内联 */
export function renderActionTags(content?: string | null): ReactNode {
  return actionTagsOf(content).map((tag) => (
    <span key={tag.label} className={`mc-mode-chip action ${tag.cls}`}>
      {tag.label}
    </span>
  ));
}