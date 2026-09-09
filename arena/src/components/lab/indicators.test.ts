/** 指标对齐/分组：K 线是尾部切片、指标是全历史，错位画出来不报错但完全是错的。 */
import { describe, expect, test } from 'vitest';
import { alignSeries, paneGroups, seriesColor, toPoints } from './indicators';
import type { IndicatorSet } from '../../api/client';

const set = (groups: string[]): IndicatorSet => ({
  bars: 10,
  overlays: [],
  panes: groups.map((g, i) => ({
    key: `${g}:0`,
    group: g,
    label: g,
    kind: 'line' as const,
    values: [i, i, i],
  })),
});

describe('alignSeries', () => {
  test('K 线是全历史时原样返回', () => {
    const v = [1, 2, 3];
    expect(alignSeries(v, 3)).toBe(v);
  });

  test('K 线只取最近 600 根 → 指标取尾部', () => {
    expect(alignSeries([1, 2, 3, 4, 5], 3)).toEqual([3, 4, 5]);
  });

  test('K 线比指标还长（数据源换了）→ 不硬切，交给调用方发现', () => {
    expect(alignSeries([1, 2], 5)).toEqual([1, 2]);
  });

  test('空窗口不返回全历史', () => {
    expect(alignSeries([1, 2, 3], 0)).toEqual([1, 2, 3]);
  });
});

describe('paneGroups', () => {
  test('同一调用点的多条线合成一个副图', () => {
    expect(paneGroups(set(['macd@10', 'macd@10', 'rsi@20']))).toEqual(['macd@10', 'rsi@20']);
  });

  test('空指标不炸', () => {
    expect(paneGroups(null)).toEqual([]);
    expect(paneGroups(undefined)).toEqual([]);
  });
});

describe('seriesColor', () => {
  test('同一调色板内按次序取色，超出则轮转', () => {
    expect(seriesColor(true, 0)).toBe(seriesColor(true, 8));
    expect(seriesColor(true, 0)).not.toBe(seriesColor(true, 1));
  });

  test('叠加线与副图用不同调色板', () => {
    expect(seriesColor(true, 0)).not.toBe(seriesColor(false, 0));
  });
});

describe('toPoints', () => {
  const bars = [{ date: '2024-01-01' }, { date: '2024-01-02' }, { date: '2024-01-03' }];

  test('等长时逐根对齐时间轴', () => {
    expect(toPoints(bars, [1, 2, 3])).toEqual([
      { time: '2024-01-01', value: 1 },
      { time: '2024-01-02', value: 2 },
      { time: '2024-01-03', value: 3 },
    ]);
  });

  test('指标是全历史、K 线只取尾部 → 对齐到最后几根', () => {
    expect(toPoints(bars, [9, 8, 7, 1, 2, 3])).toEqual([
      { time: '2024-01-01', value: 1 },
      { time: '2024-01-02', value: 2 },
      { time: '2024-01-03', value: 3 },
    ]);
  });

  test('指标比 K 线短（不同源）→ 一条都不画，避免错位', () => {
    expect(toPoints(bars, [1, 2])).toEqual([]);
  });

  test('NA 只给 time → 画成断口，不连假线', () => {
    expect(toPoints(bars, [null, 2, null])).toEqual([
      { time: '2024-01-01' },
      { time: '2024-01-02', value: 2 },
      { time: '2024-01-03' },
    ]);
  });
});
