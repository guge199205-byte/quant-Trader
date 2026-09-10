import { describe, expect, test } from 'vitest';
import { sameSymbol, stockLabel, stockName, symbolAliases } from './symbols';

const NAMES = {
  '600519.SH': '贵州茅台',
  '000001.SZ': '平安银行',
  '920950.BJ': '海泰新能',
  '00700.HK': '腾讯控股',
  NVDA: '英伟达',
};

describe('symbolAliases', () => {
  test('后缀式代码展开出前缀式与裸代码', () => {
    expect(symbolAliases('600519.SH')).toEqual(
      expect.arrayContaining(['600519.SH', 'SH600519', 'SH.600519', '600519']),
    );
  });

  test('前缀式代码展开出后缀式与裸代码', () => {
    expect(symbolAliases('SH600519')).toEqual(
      expect.arrayContaining(['600519.SH', 'SH600519', '600519']),
    );
  });

  test('SS 视为上交所别名', () => {
    expect(symbolAliases('600519.SS')).toContain('600519.SH');
  });

  test('裸 A 股代码按号段补全交易所', () => {
    expect(symbolAliases('600519')).toContain('600519.SH');
    expect(symbolAliases('300750')).toContain('300750.SZ');
    expect(symbolAliases('920950')).toContain('920950.BJ');
    expect(symbolAliases('430139')).toContain('430139.BJ');
  });

  test('裸港股 5 位代码补 .HK', () => {
    expect(symbolAliases('00700')).toEqual(expect.arrayContaining(['00700.HK', 'HK00700']));
  });

  test('美股代码原样返回，不做改写', () => {
    expect(symbolAliases('nvda')).toEqual(['NVDA']);
  });

  test('空值返回空数组', () => {
    expect(symbolAliases('')).toEqual([]);
    expect(symbolAliases(null)).toEqual([]);
  });
});

describe('stockName', () => {
  test('后缀式名称表能命中前缀式代码（通达信桥持仓写法）', () => {
    expect(stockName(NAMES, 'SH600519')).toBe('贵州茅台');
    expect(stockName(NAMES, 'SH000001')).toBe('平安银行');
    expect(stockName(NAMES, 'BJ920950')).toBe('海泰新能');
    expect(stockName(NAMES, 'HK00700')).toBe('腾讯控股');
  });

  test('裸代码能命中后缀式名称表', () => {
    expect(stockName(NAMES, '600519')).toBe('贵州茅台');
    expect(stockName(NAMES, '00700')).toBe('腾讯控股');
  });

  test('完全一致的写法直接命中', () => {
    expect(stockName(NAMES, '600519.SH')).toBe('贵州茅台');
    expect(stockName(NAMES, 'NVDA')).toBe('英伟达');
  });

  test('名称表用前缀式、查询用后缀式同样命中', () => {
    expect(stockName({ SH600519: '贵州茅台' }, '600519.SH')).toBe('贵州茅台');
  });

  test('未收录的代码返回 undefined，不抛错', () => {
    expect(stockName(NAMES, '999999.SH')).toBeUndefined();
    expect(stockName(NAMES, '')).toBeUndefined();
    expect(stockName(NAMES, null)).toBeUndefined();
  });

  test('空名称表返回 undefined', () => {
    expect(stockName(undefined, '600519.SH')).toBeUndefined();
    expect(stockName({}, '600519.SH')).toBeUndefined();
  });
});

describe('stockLabel', () => {
  test('未收录时回退原代码', () => {
    expect(stockLabel(NAMES, '999999.SH')).toBe('999999.SH');
    expect(stockLabel(NAMES, 'SH600519')).toBe('贵州茅台');
  });
});

describe('sameSymbol', () => {
  test('跨写法判定为同一只票', () => {
    expect(sameSymbol('SH600519', '600519.SH')).toBe(true);
    expect(sameSymbol('600519', '600519.SH')).toBe(true);
    expect(sameSymbol('00700', 'HK00700')).toBe(true);
  });

  test('不同票不误判', () => {
    expect(sameSymbol('600519.SH', '601318.SH')).toBe(false);
    expect(sameSymbol('', '600519.SH')).toBe(false);
  });
});
