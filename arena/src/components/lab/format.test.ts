/** 候选对比卡的取数与上色：pct 与 value 两种口径混在同一格表里，
 *  取错会显示成 0.6% 而不是 62%，方向取错会把变差染成绿色。 */
import { describe, expect, test } from 'vitest';
import { compareCellText, compareDeltaClass, compareDeltaText } from './format';

describe('compareCellText', () => {
  test('有 pct 时按百分数显示（pct 已是 62.35 口径）', () => {
    expect(compareCellText({ label: '净收益', pct: 62.35 })).toBe('62.4%');
  });

  test('值本身不带符号：回撤 18% 不能写成 +18%', () => {
    expect(compareCellText({ label: '最大回撤', pct: 18.2 })).toBe('18.2%');
  });

  test('没有 pct 时按整数显示（成交笔数这类）', () => {
    expect(compareCellText({ label: '成交笔数', value: 137 })).toBe('137');
  });

  test('两者都缺 → 破折号，不显示 0', () => {
    expect(compareCellText({ label: '夏普' })).toBe('—');
    expect(compareCellText(undefined)).toBe('—');
  });
});

describe('compareDeltaText', () => {
  test('百分数口径的变化带 pp 后缀并保留符号', () => {
    const before = { label: '最大回撤', pct: 18.2 };
    const after = { label: '最大回撤', pct: 12.5 };
    expect(compareDeltaText(before, after)).toBe('-5.7pp');
  });

  test('计数口径的变化不带 pp', () => {
    expect(compareDeltaText({ label: '成交笔数', value: 10 }, { label: '成交笔数', value: 14 }))
      .toBe('+4');
  });

  test('任意一侧缺值 → 破折号', () => {
    expect(compareDeltaText({ label: '净收益', pct: 62 }, { label: '净收益' })).toBe('—');
  });
});

describe('compareDeltaClass', () => {
  test('数值变大 → up', () => {
    expect(compareDeltaClass({ label: 'x', pct: 10 }, { label: 'x', pct: 20 })).toBe('up');
  });

  test('数值变小 → down', () => {
    expect(compareDeltaClass({ label: 'x', pct: 20 }, { label: 'x', pct: 10 })).toBe('down');
  });

  test('相等或缺值 → 不上色', () => {
    expect(compareDeltaClass({ label: 'x', pct: 10 }, { label: 'x', pct: 10 })).toBe('');
    expect(compareDeltaClass({ label: 'x' }, { label: 'x', pct: 10 })).toBe('');
  });
});
