import { describe, expect, test } from 'vitest';
import { shortName } from './ModelCard';

describe('shortName', () => {
  test('模型名 → 终端短标签（原行为不变）', () => {
    expect(shortName('deepseek-v4-pro')).toBe('DS V4-PRO');
    expect(shortName('deepseek-v4-flash')).toBe('DS V4-FLASH');
    expect(shortName('glm-5.3-flash')).toBe('GLM-5.3-FLASH');
  });

  test('agent 展示名不受短标签规则改写（market-research 不裸露英文）', () => {
    // 2026-09-12 审查 LOW：shortName 是「模型短标签」，对市场研究这类 agent 签名
    // 只会走到 .toUpperCase() 兜底 → "MARKET-RESEARCH"。模型卡 / 配置面板 / 成交
    // 事件 feed 都走它，故在源头委托 displayAgentName（展示层唯一入口）。
    expect(shortName('market-research')).toBe('市场研究');
  });
});
