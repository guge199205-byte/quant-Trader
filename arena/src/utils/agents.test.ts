import { describe, expect, test } from 'vitest';
import { displayAgentName } from './agents';

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
