import { describe, expect, it } from 'vitest';
import { dailyCloses, seriesStats } from './equity';

const pt = (date: string, value: number, ts = `${date}T15:00:00+08:00`) => ({ date, ts, value });

describe('dailyCloses', () => {
  it('同一天多点取最后一个采样', () => {
    const closes = dailyCloses([pt('2026-08-31', 100000), pt('2026-08-31', 101000), pt('2026-09-01', 99000)]);
    expect(closes).toEqual([
      { date: '2026-08-31', value: 101000 },
      { date: '2026-09-01', value: 99000 },
    ]);
  });

  it('缺 date 字段时回退 ts 前 10 位', () => {
    const closes = dailyCloses([{ ts: '2026-09-02T10:00:00+08:00', value: 5 }]);
    expect(closes).toEqual([{ date: '2026-09-02', value: 5 }]);
  });

  it('丢弃无日期或非有限值的点', () => {
    const closes = dailyCloses([{ ts: '', value: 1 }, { ts: '2026-09-02', value: NaN }]);
    expect(closes).toEqual([]);
  });
});

describe('seriesStats', () => {
  it('区间收益 = 末值 / 首值 − 1', () => {
    const s = seriesStats([pt('2026-08-31', 100000), pt('2026-09-01', 110000)]);
    expect(s?.ret).toBeCloseTo(0.1, 10);
    expect(s?.from).toBe('2026-08-31');
    expect(s?.to).toBe('2026-09-01');
    expect(s?.points).toBe(2);
  });

  it('最大回撤取正数（与后端 abs 口径一致）', () => {
    const s = seriesStats([pt('2026-08-31', 100000), pt('2026-09-01', 120000), pt('2026-09-02', 90000)]);
    expect(s?.maxDrawdown).toBeCloseTo(0.25, 10);
  });

  it('序列单调上升时回撤为 0', () => {
    const s = seriesStats([pt('2026-08-31', 100), pt('2026-09-01', 110), pt('2026-09-02', 120)]);
    expect(s?.maxDrawdown).toBe(0);
  });

  it('日收益样本不足 3 个时夏普为 null', () => {
    const s = seriesStats([pt('2026-08-31', 100), pt('2026-09-01', 101), pt('2026-09-02', 102)]);
    expect(s?.sharpe).toBeNull();
  });

  it('收益恒定（波动为 0）时夏普为 null', () => {
    const s = seriesStats([
      pt('2026-08-31', 100), pt('2026-09-01', 110), pt('2026-09-02', 121), pt('2026-09-03', 133.1), pt('2026-09-04', 146.41),
    ]);
    expect(s?.sharpe).toBeNull();
  });

  it('有波动时给出年化夏普（日频 ×√252）', () => {
    const s = seriesStats([
      pt('2026-08-31', 100), pt('2026-09-01', 110), pt('2026-09-02', 105), pt('2026-09-03', 115), pt('2026-09-04', 110),
    ]);
    expect(s?.sharpe).toBeGreaterThan(0);
    expect(s?.sharpe).toBeLessThan(100);
  });

  it('有效点不足 2 个返回 null', () => {
    expect(seriesStats([])).toBeNull();
    expect(seriesStats([pt('2026-08-31', 100000)])).toBeNull();
  });

  it('丢弃非法点后再判断样本量', () => {
    const s = seriesStats([pt('2026-08-31', 100000), { ts: '2026-09-01T10:00:00+08:00', value: NaN }]);
    expect(s).toBeNull();
  });
});
