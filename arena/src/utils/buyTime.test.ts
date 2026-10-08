import { describe, expect, test } from 'vitest';
import { deriveBuyTimes } from './buyTime';

/** 富途订单历史 → 建仓时间（FIFO：买建仓、卖冲销，取仍有剩余的最老批次）。
 *  港股桥回报不带买入时刻（映射器硬编码空串），用它回填「持仓」tab 的日期筛选。 */

const deal = (ts: string, code: string, side: string, volume: number) => ({ ts, code, side, volume });

describe('deriveBuyTimes', () => {
  test('卖光后再买：建仓时间取新批次', () => {
    expect(
      deriveBuyTimes([
        deal('2026-09-11T09:37:26', '00700', 'BUY', 700),
        deal('2026-09-14T10:23:02', '00700', 'SELL', 700),
        deal('2026-09-14T11:07:27', '00700', 'BUY', 100),
      ]),
    ).toEqual({ '00700': '2026-09-14T11:07' });
  });

  test('部分卖出：保留最老批次的建仓时间（FIFO 先冲老仓）', () => {
    expect(
      deriveBuyTimes([
        deal('2026-09-11T09:00:00', '00700', 'BUY', 500),
        deal('2026-09-14T09:00:00', '00700', 'BUY', 300),
        deal('2026-09-15T09:00:00', '00700', 'SELL', 500),
      ]),
    ).toEqual({ '00700': '2026-09-14T09:00' });
  });

  test('清仓的票不出现在结果里', () => {
    expect(
      deriveBuyTimes([
        deal('2026-09-11T09:00:00', '00700', 'BUY', 300),
        deal('2026-09-15T09:00:00', '00700', 'SELL', 300),
      ]),
    ).toEqual({});
  });

  test('卖出多于在册（历史窗口外的买入）只冲已知部分，不产生负库存', () => {
    expect(
      deriveBuyTimes([
        deal('2026-09-15T09:00:00', '00700', 'SELL', 300),
        deal('2026-09-15T10:00:00', '00700', 'BUY', 100),
      ]),
    ).toEqual({ '00700': '2026-09-15T10:00' });
  });

  test('大小写与零成交量容错', () => {
    expect(
      deriveBuyTimes([
        deal('2026-09-14T11:07:27', '00700', 'buy', 0),
        deal('2026-09-14T11:08:50', '00700', 'Buy', 200),
      ]),
    ).toEqual({ '00700': '2026-09-14T11:08' });
  });

  test('多票互不干扰，时间戳统一截到分钟（与桥口径一致）', () => {
    const out = deriveBuyTimes([
      deal('2026-09-14T11:07:27.123', '00700', 'BUY', 100),
      deal('2026-09-14T11:08:50.456', '09988', 'BUY', 200),
    ]);
    expect(out).toEqual({ '00700': '2026-09-14T11:07', '09988': '2026-09-14T11:08' });
  });

  test('空数组 / 空 code 不产出', () => {
    expect(deriveBuyTimes([])).toEqual({});
    expect(deriveBuyTimes([deal('2026-09-14T11:07:27', '', 'BUY', 100)])).toEqual({});
  });
});