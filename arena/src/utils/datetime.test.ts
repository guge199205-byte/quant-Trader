import { describe, expect, test } from 'vitest';
import { fmtAgo, fmtClock, fmtDateTime, fmtDay, fmtSpan, fmtWeekday } from './datetime';

const TS = '2026-09-10T14:45:02.140557+08:00';

describe('fmtDateTime', () => {
  test('带微秒与时区的 ISO 截到秒', () => {
    expect(fmtDateTime(TS)).toBe('2026-09-10 14:45:02');
  });

  test('不带秒的时间戳补 :00，列宽一致', () => {
    expect(fmtDateTime('2026-09-10T14:45')).toBe('2026-09-10 14:45:00');
  });

  test('可选只到分', () => {
    expect(fmtDateTime(TS, false)).toBe('2026-09-10 14:45');
  });

  test('空值显示占位符', () => {
    expect(fmtDateTime(null)).toBe('—');
    expect(fmtDateTime('')).toBe('—');
  });

  test('非法格式原样截断，不返回 Invalid Date', () => {
    expect(fmtDateTime('2026/09/10 14:45:02')).toBe('2026/09/10 14:45:02');
  });
});

describe('fmtClock / fmtDay', () => {
  test('取时间与日期部分', () => {
    expect(fmtClock(TS)).toBe('14:45:02');
    expect(fmtClock(TS, false)).toBe('14:45');
    expect(fmtDay(TS)).toBe('2026-09-10');
  });
});

describe('fmtWeekday', () => {
  test('按记录的日历日算星期（2026-09-10 是周四）', () => {
    expect(fmtWeekday(TS)).toBe('周四');
  });

  test('无时间戳返回空串', () => {
    expect(fmtWeekday(null)).toBe('');
  });
});

describe('fmtAgo', () => {
  const now = new Date('2026-09-10T15:45:02+08:00').getTime();

  test('分级：秒/分/小时/天', () => {
    expect(fmtAgo('2026-09-10T15:44:32+08:00', now)).toBe('30 秒前');
    expect(fmtAgo('2026-09-10T15:15:02+08:00', now)).toBe('30 分钟前');
    expect(fmtAgo('2026-09-10T12:45:02+08:00', now)).toBe('3 小时 0 分前');
    expect(fmtAgo('2026-09-08T10:45:02+08:00', now)).toBe('2 天 5 小时前');
  });

  test('未来时间显示 0 秒前，不出现负值', () => {
    expect(fmtAgo('2026-09-10T16:00:00+08:00', now)).toBe('0 秒前');
  });

  test('空值返回空串', () => {
    expect(fmtAgo(null, now)).toBe('');
  });
});

describe('fmtSpan', () => {
  test('精确到分：分钟/小时/天', () => {
    expect(fmtSpan(30 / 1440)).toBe('30 分');
    expect(fmtSpan(5.2 / 24)).toBe('5 小时 12 分');
    expect(fmtSpan(3)).toBe('3 天');
    expect(fmtSpan(2.5)).toBe('2 天 12 小时');
  });

  test('空值显示占位符', () => {
    expect(fmtSpan(null)).toBe('—');
  });
});
