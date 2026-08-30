/** 数值格式化工具（终端风格：定宽数字、显式符号） */

export const fmtMoney = (v: number | null | undefined, currency = '$', digits = 0): string => {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${currency}${v.toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 })}`;
};

export const fmtPct = (v: number | null | undefined, digits = 2, signed = true): string => {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = signed && v > 0 ? '+' : '';
  return `${sign}${(v * 100).toFixed(digits)}%`;
};

export const fmtNum = (v: number | null | undefined, digits = 2): string => {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
};

export const fmtDate = (d: string | null | undefined): string => {
  if (!d) return '—';
  return d.slice(0, 10);
};

export const pnlClass = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v)) return 'dim';
  if (v > 0) return 'up';
  if (v < 0) return 'down';
  return 'dim';
};
