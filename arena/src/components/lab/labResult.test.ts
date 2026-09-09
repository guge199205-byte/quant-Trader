/** 两条回测链路 → LabResult 的适配器。重点防「report.trades 是笔数不是明细」这个陷阱：
 *  写错的话逐笔表会渲染成一个数字，而图表看不出异常。 */
import { describe, expect, test } from 'vitest';
import type { BtResult, PineReport } from '../../api/client';
import { fromPineReport, fromTemplate } from './labResult';
import { fmt, fmtMoney, fmtPct } from './format';

const TEMPLATE_RESULT: BtResult = {
  stats: {
    'Net profit': { value: 262899.93, pct: 262.9 },
    'Sharpe ratio': { value: 1.23, pct: null },
  },
  trades: [
    {
      entry_time: '2016-02-24 09:30:00',
      entry_price: 13.93,
      qty: 6748,
      signal: 'L',
      exit_time: '2016-03-01 09:30:00',
      exit_price: 12.86,
      profit: -7310.75,
      profit_pct: -7.77,
    },
  ],
  equity: [
    { date: '2016-01-04', value: 100000 },
    { date: '2026-09-08', value: 358496.41 },
  ],
  meta: {
    strategy: 'sma_cross',
    name: '双均线交叉',
    symbol: '600309.SH',
    name_cn: '万华化学',
    adj: 'backward',
    start: '2016-01-04',
    end: '2026-09-08',
    bars: 2596,
    params: { fast: 5, slow: 20 },
  },
};

/** 取自 data/pine_transpile/0001/report.json 的真实形状 */
const PINE_REPORT: PineReport = {
  id: '0001',
  symbol: '600309.SH',
  adj: 'backward',
  trades: 2,
  stats: {
    'Net profit': { value: 6234.96, pct: 62.35 },
    'Buy & hold return': { value: 55510.63, pct: 555.11 },
  },
  trade_rows: [
    {
      entry_time: '2016-04-08 09:30:00',
      entry_price: 14.76,
      qty: 668,
      signal: 'L',
      exit_time: '2016-04-13 09:30:00',
      exit_price: 15.48,
      profit: 470.86,
      profit_pct: 4.77,
    },
    {
      entry_time: '2016-05-03 09:30:00',
      entry_price: 15.1,
      qty: 660,
      signal: 'L',
      exit_time: '2016-05-09 09:30:00',
      exit_price: 14.4,
      profit: -470.0,
      profit_pct: -4.6,
    },
  ],
  equity: [
    { date: '2016-01-04', value: 10000 },
    { date: '2026-09-07', value: 16234.96 },
  ],
};

describe('fromPineReport', () => {
  test('用 trade_rows 当明细，而不是把 trades 笔数当明细', () => {
    // Act
    const r = fromPineReport(PINE_REPORT, '斐波那契云');

    // Assert
    expect(r.trades).toHaveLength(2);
    expect(r.trades[0].entry_time).toBe('2016-04-08 09:30:00');
    expect(r.trades[0].entry_price).toBe(14.76);
  });

  test('笔数写进副标题，不冒充明细长度', () => {
    const r = fromPineReport(PINE_REPORT, '斐波那契云');
    expect(r.subtitle).toContain('2 笔成交');
  });

  test('本金取自净值首点，且不标货币（引擎口径 10000 非 10 万）', () => {
    const r = fromPineReport(PINE_REPORT, '斐波那契云');
    expect(r.capital).toBe(10000);
    expect(r.unit).toBe('');
  });

  test('标的与复权口径原样带出，供K线对齐', () => {
    const r = fromPineReport(PINE_REPORT, '斐波那契云');
    expect(r.symbol).toBe('600309.SH');
    expect(r.adj).toBe('backward');
  });

  test('缺 title 时回落到 id', () => {
    const r = fromPineReport(PINE_REPORT);
    expect(r.title).toBe('0001');
  });
});

describe('fromTemplate', () => {
  test('trades 本身就是明细数组', () => {
    const r = fromTemplate(TEMPLATE_RESULT);
    expect(r.trades).toHaveLength(1);
    expect(r.trades[0].entry_price).toBe(13.93);
  });

  test('本金取净值首点、单位是人民币', () => {
    const r = fromTemplate(TEMPLATE_RESULT);
    expect(r.capital).toBe(100000);
    expect(r.unit).toBe('¥');
  });

  test('stats 缺失的 pct 收敛成 null 而不是 undefined', () => {
    const r = fromTemplate(TEMPLATE_RESULT);
    expect(r.stats['Sharpe ratio']).toEqual({ value: 1.23, pct: null });
  });

  test('source 标成 template，标题用 meta.name', () => {
    const r = fromTemplate(TEMPLATE_RESULT);
    expect(r.source).toBe('template');
    expect(r.title).toBe('双均线交叉');
  });
});

describe('indicators 归一化', () => {
  test('老报告没有 indicators → 空集，不是 undefined', () => {
    const r = fromPineReport(PINE_REPORT);
    expect(r.indicators).toEqual({ bars: 0, overlays: [], panes: [] });
  });

  test('后端给空对象 {} → 收敛成空集（空对象是 truthy，直接读 .overlays 会炸）', () => {
    const r = fromPineReport({ ...PINE_REPORT, indicators: {} as never });
    expect(r.indicators.overlays).toEqual([]);
    expect(r.indicators.panes).toEqual([]);
  });

  test('有指标时原样带出', () => {
    const set = {
      bars: 3,
      overlays: [
        { key: 'k', group: 'g', label: 'EMA(20)', kind: 'line' as const, values: [1, 2, 3] },
      ],
      panes: [],
    };
    const r = fromPineReport({ ...PINE_REPORT, indicators: set });
    expect(r.indicators.overlays[0].label).toBe('EMA(20)');
    expect(r.indicators.bars).toBe(3);
  });
});

describe('格式化', () => {
  test('空值一律给破折号', () => {
    expect(fmt(null)).toBe('—');
    expect(fmtPct(undefined)).toBe('—');
    expect(fmtMoney(NaN)).toBe('—');
  });

  test('百分比带正负号，金额按单位前缀', () => {
    expect(fmtPct(4.77)).toBe('+4.77%');
    expect(fmtPct(-4.6)).toBe('-4.60%');
    expect(fmtMoney(6234.96)).toBe('¥6,235');
    expect(fmtMoney(6234.96, '')).toBe('6,235');
  });
});
