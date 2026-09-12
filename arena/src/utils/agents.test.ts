import { describe, expect, test } from 'vitest';
import { displayAgentName, rankPerformers } from './agents';

describe('displayAgentName', () => {
  test('market-research 显示为中文（目录名是英文，UI 不该裸露）', () => {
    expect(displayAgentName('market-research')).toBe('市场研究');
  });

  test('模型名原样返回（签名即展示名）', () => {
    expect(displayAgentName('deepseek-v4-pro')).toBe('deepseek-v4-pro');
    expect(displayAgentName('glm-5.3-flash')).toBe('glm-5.3-flash');
  });

  test('空值原样返回，不臆造名字', () => {
    expect(displayAgentName('')).toBe('');
    expect(displayAgentName(null)).toBe('');
    expect(displayAgentName(undefined)).toBe('');
  });
});

describe('rankPerformers', () => {
  test('最高/最低按收益排序，展示名走 displayAgentName', () => {
    const { highest, lowest } = rankPerformers([
      { name: 'deepseek-v4-pro', ret: 0.012 },
      { name: 'glm-5.3-flash', ret: -0.03 },
      { name: 'deepseek-v4-flash', ret: 0.004 },
    ]);
    expect(highest?.name).toBe('deepseek-v4-pro');
    expect(highest?.ret).toBe(0.012);
    expect(lowest?.name).toBe('glm-5.3-flash');
  });

  test('研究线排最高时也不裸露英文签名（cn 下跌行情里的常态）', () => {
    // 2026-09-12 审查 LOW：Live 顶部「最高/最低」chip 曾直接渲染 r.name，
    // 下跌行情里 0% 的研究线常排最高，页面裸露 market-research。
    const { highest, lowest } = rankPerformers([
      { name: 'market-research', ret: 0 },
      { name: 'glm-5.3-flash', ret: -0.052 },
    ]);
    expect(highest?.name).toBe('市场研究');
    expect(lowest?.name).toBe('glm-5.3-flash');
  });

  test('无收益数据 → highest/lowest 为 null（页面照旧渲染 —）', () => {
    expect(rankPerformers([])).toEqual({ highest: null, lowest: null });
    expect(rankPerformers([{ name: 'market-research', ret: null }])).toEqual({
      highest: null,
      lowest: null,
    });
  });
});
