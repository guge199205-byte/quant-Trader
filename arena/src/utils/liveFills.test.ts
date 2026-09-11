import { describe, expect, test } from 'vitest';
import { attachFillsToRounds, LiveFill, toLiveAdjust, toLiveFill } from './liveFills';
import type { LiveTradeLog } from '../api/client';

const log = (over: Partial<LiveTradeLog> = {}): LiveTradeLog => ({
  ts: '2026-09-08T09:37:42.449843+08:00',
  mode: 'execute',
  code: '688183.SH',
  side: 'sell',
  volume: 200,
  price: 131.69,
  fill: { order_id: '55110', filled_price: 131.69, filled_volume: 200 },
  ...over,
});

describe('toLiveFill', () => {
  test('归一化成交回报（取 filled_price 与订单号）', () => {
    expect(toLiveFill(log())).toEqual({
      ts: '2026-09-08T09:37:42.449843+08:00',
      side: 'sell',
      code: '688183.SH',
      volume: 200,
      price: 131.69,
      costPrice: null,
      orderId: '55110',
      mode: 'execute',
    });
  });

  test('方向缺失 / 数量为 0 的记录不算成交', () => {
    expect(toLiveFill(log({ side: undefined }))).toBeNull();
    expect(toLiveFill(log({ volume: 0 }))).toBeNull();
    expect(toLiveFill(null)).toBeNull();
  });

  test('成本价为 0（桥未回传）记为 null，避免下游算成 0 成本', () => {
    expect(toLiveFill(log({ cost_price: 0 }))?.costPrice).toBeNull();
    expect(toLiveFill(log({ cost_price: 126.148 }))?.costPrice).toBe(126.148);
  });

  test('无成交价时置 null 而非 0', () => {
    expect(toLiveFill(log({ price: null, fill: null }))?.price).toBeNull();
  });
});

describe('toLiveAdjust', () => {
  // 09-08 实录行：pro 误卖 flash 的 688183 后，人工对账把卖款归回 flash
  const adjust = (over: Partial<LiveTradeLog> = {}): LiveTradeLog => ({
    ts: '2026-09-08T13:24:42.211460+08:00',
    mode: 'fill_adjust',
    agent: 'deepseek-v4-flash',
    code: '688183.SH',
    volume: 200,
    price: 131.69,
    note: '对账：09:37 pro 空账本兜底事故误卖本仓，卖款 ¥26,338 归属 flash，幽灵仓清出',
    ...over,
  });

  test('对账行归一化（保留 note/归属与数量/价格）', () => {
    expect(toLiveAdjust(adjust())).toEqual({
      ts: '2026-09-08T13:24:42.211460+08:00',
      code: '688183.SH',
      volume: 200,
      price: 131.69,
      note: '对账：09:37 pro 空账本兜底事故误卖本仓，卖款 ¥26,338 归属 flash，幽灵仓清出',
      agent: 'deepseek-v4-flash',
      mode: 'fill_adjust',
    });
  });

  test('缺 agent 的对账行归属 null（不硬塞给任何人）', () => {
    expect(toLiveAdjust(adjust({ agent: undefined }))?.agent).toBeNull();
    expect(toLiveAdjust(adjust({ agent: '  ' }))?.agent).toBeNull();
  });

  test('成交行不是对账行（两类互斥，避免图上重复画标记）', () => {
    expect(toLiveAdjust(log())).toBeNull();
    expect(toLiveFill(adjust())).toBeNull(); // 反向亦然：对账行没有方向
  });

  test('缺 note 的对账行不成立（无法解释的校正不如不显示）', () => {
    expect(toLiveAdjust(adjust({ note: undefined }))).toBeNull();
    expect(toLiveAdjust(adjust({ note: '   ' }))).toBeNull();
    expect(toLiveAdjust(null)).toBeNull();
  });

  test('价格缺失置 null、数量缺失置 0，不编造数字', () => {
    const a = toLiveAdjust(adjust({ price: undefined, volume: undefined as unknown as number }));
    expect(a?.price).toBeNull();
    expect(a?.volume).toBe(0);
  });
});

const fill = (ts: string, over: Partial<LiveFill> = {}): LiveFill => ({
  ts,
  side: 'sell',
  code: '688183.SH',
  volume: 100,
  price: 118.92,
  costPrice: null,
  orderId: null,
  mode: 'execute',
  ...over,
});

const ROUNDS = [
  { seq: 1, ts: '2026-09-04T11:00:00+08:00' },
  { seq: 2, ts: '2026-09-04T14:00:00+08:00' },
  { seq: 3, ts: '2026-09-04T14:45:02+08:00' },
];

describe('attachFillsToRounds', () => {
  test('成交归到最近一个不晚于它的回合', () => {
    const { bySeq, orphan } = attachFillsToRounds(ROUNDS, [
      fill('2026-09-04T14:02:18+08:00'),
      fill('2026-09-04T14:52:30+08:00'),
    ]);
    expect(bySeq.get(2)?.length).toBe(1);
    expect(bySeq.get(3)?.length).toBe(1);
    expect(orphan).toEqual([]);
  });

  test('间隔超过 maxGapMin 的成交不硬塞（隔夜跳空）', () => {
    const { bySeq, orphan } = attachFillsToRounds(ROUNDS, [fill('2026-09-05T09:40:00+08:00')]);
    expect(bySeq.size).toBe(0);
    expect(orphan.length).toBe(1);
  });

  test('早于本页最早回合的成交列为未归属', () => {
    const { orphan } = attachFillsToRounds(ROUNDS, [fill('2026-09-04T09:30:00+08:00')]);
    expect(orphan.length).toBe(1);
  });

  test('同一回合多笔成交按时间升序', () => {
    const { bySeq } = attachFillsToRounds(ROUNDS, [
      fill('2026-09-04T14:30:00+08:00'),
      fill('2026-09-04T14:10:00+08:00'),
    ]);
    expect(bySeq.get(2)?.map((f) => f.ts)).toEqual([
      '2026-09-04T14:10:00+08:00',
      '2026-09-04T14:30:00+08:00',
    ]);
  });

  test('回合没有时间戳时不影响归属（跳过该回合）', () => {
    const { bySeq } = attachFillsToRounds(
      [{ seq: 1, ts: null }, ...ROUNDS],
      [fill('2026-09-04T14:02:18+08:00')],
    );
    expect(bySeq.get(2)?.length).toBe(1);
  });
});
