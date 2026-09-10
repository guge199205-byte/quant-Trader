import { describe, expect, it } from 'vitest';
import { fmtMoney, fmtMoneySigned, fmtPrice } from './format';

describe('fmtMoneySigned', () => {
  it('盈利带 +、亏损带 −（币种符号在数字前，号在最前）', () => {
    expect(fmtMoneySigned(5705, '¥')).toBe('+¥5,705');
    expect(fmtMoneySigned(-8.8, '¥')).toBe('-¥9');
    expect(fmtMoneySigned(-1234.5, '$', 0)).toBe('-$1,235');
  });

  // 2026-09-11 实录：持仓表用 Math.abs 抹掉负号，福恩股份 -¥8.8 显示成「¥9」——
  // 亏损在文本上读起来是盈利，只有颜色不同。符号必须落到字面上。
  it('亏损绝不显示成无符号金额', () => {
    const out = fmtMoneySigned(-8.8, '¥');
    expect(out).not.toBe('¥9');
    expect(out.startsWith('-')).toBe(true);
  });

  it('零不带符号', () => {
    expect(fmtMoneySigned(0, '¥')).toBe('¥0');
  });

  it('null / NaN → 破折号（拿不到就说拿不到）', () => {
    expect(fmtMoneySigned(null, '¥')).toBe('—');
    expect(fmtMoneySigned(undefined, '¥')).toBe('—');
    expect(fmtMoneySigned(NaN, '¥')).toBe('—');
  });

  it('小数位可指定（成交表按分显示）', () => {
    expect(fmtMoneySigned(-12.345, '¥', 2)).toBe('-¥12.35');
  });
});

describe('fmtPrice', () => {
  it('保留两位小数（A股个股报价）', () => {
    expect(fmtPrice(118.92, '¥')).toBe('¥118.92');
    expect(fmtPrice(78, '¥')).toBe('¥78.00');
  });

  it('基金/ETF 保留三位小数', () => {
    expect(fmtPrice(1.995, '¥')).toBe('¥1.995');
    expect(fmtPrice(5.268, '¥')).toBe('¥5.268');
  });

  it('0 / null / NaN 视为「拿不到」→ 破折号', () => {
    expect(fmtPrice(0, '¥')).toBe('—');
    expect(fmtPrice(null)).toBe('—');
    expect(fmtPrice(undefined)).toBe('—');
    expect(fmtPrice(NaN)).toBe('—');
  });

  it('与 fmtMoney 的区别：价格不被抹成整数', () => {
    expect(fmtMoney(118.92, '¥')).toBe('¥119');
    expect(fmtPrice(118.92, '¥')).toBe('¥118.92');
  });
});
