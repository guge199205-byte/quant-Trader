/** 中栏结果区：K线（带买卖点）+ 净值 + 统计卡 + 逐笔。
 *
 * 内置模板与 Pine 库策略两条链路跑完都落到同一个 `LabResult`，这里只负责画，
 * 不关心策略从哪来——合并页能做到「选中即同屏对比」的关键。
 */
import { useMemo } from 'react';
import EquityChart, { type ChartLine } from '../EquityChart';
import KLineChart from '../KLineChart';
import type { Kline } from '../../api/client';
import type { LabResult } from './labResult';
import { fmt, fmtMoney, fmtPct } from './format';

const TRADE_ROWS = 60;

function StatCards({ result }: { result: LabResult }) {
  const st = result.stats;
  const cards: { label: string; value: string; tone?: 'up' | 'down' }[] = [
    {
      label: '策略净收益',
      value: `${fmtMoney(st['Net profit']?.value, result.unit)} (${fmtPct(st['Net profit']?.pct)})`,
      tone: (st['Net profit']?.pct ?? 0) >= 0 ? 'up' : 'down',
    },
    { label: '买入持有', value: fmtPct(st['Buy & hold return']?.pct) },
    { label: '最大回撤', value: fmtPct(-(st['Max equity drawdown']?.pct ?? 0)) },
    { label: '胜率', value: `${fmt(st['Percent profitable']?.pct)}%` },
    { label: '盈亏比', value: fmt(st['Profit factor']?.value) },
    { label: '夏普', value: fmt(st['Sharpe ratio']?.value) },
    { label: '索提诺', value: fmt(st['Sortino ratio']?.value) },
    { label: '成交笔数', value: fmt(st['Total trades']?.value, 0) },
    { label: '平均持仓(根)', value: fmt(st['Avg # bars in trades']?.value) },
  ];
  return (
    <div className="lab-stats">
      {cards.map((c) => (
        <div key={c.label} className={`lab-stat ${c.tone ?? ''}`}>
          <span className="k">{c.label}</span>
          <span className="v">{c.value}</span>
        </div>
      ))}
    </div>
  );
}

function TradeTable({ result }: { result: LabResult }) {
  const rows = useMemo(() => result.trades.slice(-TRADE_ROWS).reverse(), [result]);
  if (!rows.length) return null;
  return (
    <section className="lab-trades">
      <div className="lab-block-title">
        逐笔成交（共 {result.trades.length} 笔，显示最近 {rows.length} 笔）
      </div>
      <div className="lab-table-wrap">
        <table>
          <thead>
            <tr>
              <th>买入时间</th>
              <th>买入价</th>
              <th>卖出时间</th>
              <th>卖出价</th>
              <th>盈亏</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t, i) => (
              <tr
                key={`${t.entry_time}-${i}`}
                className={(t.profit ?? 0) >= 0 ? 'up' : 'down'}
              >
                <td>{t.entry_time?.slice(0, 10)}</td>
                <td>{fmt(t.entry_price)}</td>
                <td>{t.exit_time?.slice(0, 10) ?? '持仓中'}</td>
                <td>{fmt(t.exit_price)}</td>
                <td>{t.profit === null ? '—' : fmtPct(t.profit_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ResultView({
  bars,
  barsBusy,
  symbol,
  result,
  showMarkers,
  onToggleMarkers,
  fullSpan,
  onToggleSpan,
  onSyncSymbol,
}: {
  bars: Kline[];
  barsBusy: boolean;
  symbol: string;
  result: LabResult | null;
  showMarkers: boolean;
  onToggleMarkers: () => void;
  fullSpan: boolean;
  onToggleSpan: () => void;
  onSyncSymbol: () => void;
}) {
  const equityLines = useMemo<ChartLine[]>(
    () =>
      result
        ? [
            {
              id: 'equity',
              label: '策略净值',
              color: '#1a1a1a',
              points: result.equity.map((p) => ({ t: Date.parse(p.date), v: p.value })),
            },
          ]
        : [],
    [result],
  );

  return (
    <div className="lab-center">
      {result?.symbol && result.symbol !== symbol && (
        <div className="lab-warn">
          成交点属于 {result.symbol}（{result.adj}），与当前K线 {symbol} 不是同一只——
          <button onClick={onSyncSymbol}>切到 {result.symbol}</button>
        </div>
      )}
      <section className="lab-chart">
        {barsBusy && <div className="lab-mask">加载中…</div>}
        <KLineChart
          bars={bars}
          trades={result?.trades ?? []}
          height={460}
          showMarkers={showMarkers}
        />
        <div className="lab-hint">
          quantdb 日线 · {bars.length} 根 · {bars[0]?.date ?? '—'} ~{' '}
          {bars[bars.length - 1]?.date ?? '—'}
          <button className="lab-span" onClick={onToggleSpan}>
            {fullSpan ? '近 600 根' : '全历史'}
          </button>
          <button className="lab-span" onClick={onToggleMarkers}>
            {showMarkers ? '隐藏成交点' : '显示成交点'}
          </button>
        </div>
      </section>

      {result && (
        <>
          <section className="lab-block">
            <div className="lab-block-title">
              结果 · {result.title} @ {result.symbol}
              <span className="lab-tag-src">
                {result.source === 'pine' ? '策略库' : '内置模板'}
              </span>
            </div>
            <StatCards result={result} />
          </section>

          <section className="lab-equity">
            <div className="lab-block-title">
              净值曲线（初始 {result.unit}
              {result.capital.toLocaleString('zh-CN')}）
              {result.source === 'pine' && (
                <span className="lab-tag-src">本金取自候选的 initial_capital</span>
              )}
            </div>
            <EquityChart lines={equityLines} currency={result.unit} mode="dollar" height={200} />
          </section>

          <TradeTable result={result} />
        </>
      )}
    </div>
  );
}
