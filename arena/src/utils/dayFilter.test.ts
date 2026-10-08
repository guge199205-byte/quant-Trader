import { describe, expect, test } from 'vitest';
import { availableDays, dayHit, dayOf, dayOptions } from './dayFilter';

describe('dayOf', () => {
  test('ISO 时间戳（带 T / 带空格 / 纯日期）取日期部分', () => {
    expect(dayOf('2026-09-11T09:37:26.903941+08:00')).toBe('2026-09-11');
    expect(dayOf('2026-09-11 09:37:26')).toBe('2026-09-11');
    expect(dayOf('2026-09-11')).toBe('2026-09-11');
  });

  test('空值 / 非法值返回空串（不参与筛选）', () => {
    expect(dayOf(null)).toBe('');
    expect(dayOf(undefined)).toBe('');
    expect(dayOf('')).toBe('');
    expect(dayOf('—')).toBe('');
    expect(dayOf('09-11')).toBe('');
  });

  test('非法月/日（脏值）不当作日期', () => {
    expect(dayOf('2026-13-45T00:00:00')).toBe('');
    expect(dayOf('2026-00-10')).toBe('');
    expect(dayOf('2026-09-00')).toBe('');
  });
});

describe('availableDays', () => {
  test('去重、降序（最新在前），剔除空日期', () => {
    expect(
      availableDays(['2026-09-08T10:00:00', '2026-09-11T09:37:00', '', '2026-09-08', null]),
    ).toEqual(['2026-09-11', '2026-09-08']);
  });

  test('全空返回空数组', () => {
    expect(availableDays([])).toEqual([]);
    expect(availableDays([null, '', '—'])).toEqual([]);
  });
});

describe('dayHit', () => {
  test("'all' 命中一切（含无日期行）", () => {
    expect(dayHit('2026-09-11T09:37:00', 'all')).toBe(true);
    expect(dayHit('', 'all')).toBe(true);
    expect(dayHit(null, 'all')).toBe(true);
  });

  test('具体日期只命中当天；无日期行不命中', () => {
    expect(dayHit('2026-09-11T09:37:00', '2026-09-11')).toBe(true);
    expect(dayHit('2026-09-10T09:37:00', '2026-09-11')).toBe(false);
    expect(dayHit('', '2026-09-11')).toBe(false);
  });
});

describe('dayOptions', () => {
  test('数据候选 + 当前选中值（跨 tab/市场保留筛选时不丢选项），降序', () => {
    expect(dayOptions(['2026-09-11T09:37:00', '2026-09-08'], '2026-09-09')).toEqual([
      '2026-09-11',
      '2026-09-09',
      '2026-09-08',
    ]);
  });

  test("'all' 不掺进候选", () => {
    expect(dayOptions(['2026-09-11'], 'all')).toEqual(['2026-09-11']);
  });
});