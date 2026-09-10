import { describe, expect, test } from 'vitest';
import { diffSnapshots } from './positions';
import type { PositionRecord } from '../api/client';

const rec = (
  date: string,
  positions: Record<string, number>,
  this_action?: PositionRecord['this_action'],
): PositionRecord => ({ date, id: 0, positions, this_action });

describe('diffSnapshots', () => {
  test('识别新开 / 加仓 / 减仓 / 清仓', () => {
    const diffs = diffSnapshots([
      rec('2026-09-01', { CASH: 100000, '600519.SH': 0, '601318.SH': 0 }),
      rec('2026-09-02', { CASH: 40000, '600519.SH': 100, '601318.SH': 300 }),
      rec('2026-09-03', { CASH: 30000, '600519.SH': 200, '601318.SH': 100 }),
      rec('2026-09-04', { CASH: 50000, '600519.SH': 0, '601318.SH': 100 }),
    ]);
    const byDate = new Map(diffs.map((d) => [d.date, d]));
    expect(byDate.get('2026-09-02')?.changes).toEqual([
      { symbol: '601318.SH', from: 0, to: 300, delta: 300, kind: 'open' },
      { symbol: '600519.SH', from: 0, to: 100, delta: 100, kind: 'open' },
    ]);
    expect(byDate.get('2026-09-03')?.changes).toEqual([
      { symbol: '601318.SH', from: 300, to: 100, delta: -200, kind: 'reduce' },
      { symbol: '600519.SH', from: 100, to: 200, delta: 100, kind: 'add' },
    ]);
    expect(byDate.get('2026-09-04')?.changes).toEqual([
      { symbol: '600519.SH', from: 200, to: 0, delta: -200, kind: 'close' },
    ]);
  });

  test('结果按日期降序（最新在前），并带上当日动作与持仓只数', () => {
    const diffs = diffSnapshots([
      rec('2026-09-01', { CASH: 100000, '600519.SH': 0 }),
      rec('2026-09-02', { CASH: 40000, '600519.SH': 100 }, { action: 'buy', symbol: '600519.SH', amount: 100 }),
    ]);
    expect(diffs.map((d) => d.date)).toEqual(['2026-09-02', '2026-09-01']);
    expect(diffs[0].action).toEqual({ action: 'buy', symbol: '600519.SH', amount: 100 });
    expect(diffs[0].holdings).toBe(1);
    expect(diffs[0].cash).toBe(40000);
  });

  test('CASH 不参与持仓变动，但会带出现金余额', () => {
    const diffs = diffSnapshots([rec('2026-09-01', { CASH: 88888, '600519.SH': 0 })]);
    expect(diffs[0].changes).toEqual([]);
    expect(diffs[0].cash).toBe(88888);
  });

  test('无变化的日子输出空变动列表（不重复全量持仓）', () => {
    const diffs = diffSnapshots([
      rec('2026-09-01', { '600519.SH': 100 }),
      rec('2026-09-02', { '600519.SH': 100 }),
    ]);
    expect(diffs[0].changes).toEqual([]);
  });

  test('空记录返回空数组', () => {
    expect(diffSnapshots([])).toEqual([]);
  });
});
