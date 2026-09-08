/** 行情回测：quantdb 日线看盘 + 策略回测（PyneCore / Pine 运行时）。
 *
 * 数据：quantdb 后复权/不复权/前复权日线（全市场，非仅指数成分）
 * 回测：A股多头口径（T+1 由模板持仓周期自然满足，费率含印花税摊薄）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import EquityChart, { type ChartLine } from '../components/EquityChart';
import KLineChart from '../components/KLineChart';
import {
  fetchLabKlines,
  fetchLabStrategies,
  runLabBacktest,
  searchLabSymbols,
  type AdjMode,
  type BtResult,
  type Kline,
  type LabStrategy,
  type LabSymbol,
} from '../api/client';
import './MarketLab.css';

const ADJ_LABELS: { id: AdjMode; label: string }[] = [
  { id: 'unadjusted', label: '不复权' },
  { id: 'forward', label: '前复权' },
  { id: 'backward', label: '后复权' },
];

const fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(digits);

const fmtPct = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;

const fmtMoney = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;

export default function MarketLab() {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<LabSymbol[]>([]);
  const [showHits, setShowHits] = useState(false);
  const [symbol, setSymbol] = useState('600309.SH');
  const [stockName, setStockName] = useState('');
  const [adj, setAdj] = useState<AdjMode>('unadjusted');
  const [bars, setBars] = useState<Kline[]>([]);
  const [chartBusy, setChartBusy] = useState(false);

  const [strategies, setStrategies] = useState<LabStrategy[]>([]);
  const [sid, setSid] = useState('');
  const [params, setParams] = useState<Record<string, number>>({});
  const [bt, setBt] = useState<BtResult | null>(null);
  const [btBusy, setBtBusy] = useState(false);
  const [err, setErr] = useState('');

  const timer = useRef<number | null>(null);

  // ---- 策略列表 ----
  useEffect(() => {
    fetchLabStrategies()
      .then((list) => {
        setStrategies(list);
        if (list.length) {
          setSid(list[0].id);
          setParams(Object.fromEntries(list[0].params.map((p) => [p.k, p.default])));
        }
      })
      .catch(() => setErr('策略列表加载失败（后端 /api/market-lab 未就绪？）'));
  }, []);

  // ---- K线 ----
  const loadBars = useCallback(
    async (sym: string, mode: AdjMode) => {
      setChartBusy(true);
      setErr('');
      try {
        const r = await fetchLabKlines(sym, mode, 600);
        setBars(r.bars);
        setStockName(r.name);
      } catch {
        setBars([]);
        setErr(`K线加载失败：${sym}`);
      } finally {
        setChartBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (symbol) void loadBars(symbol, adj);
  }, [symbol, adj, loadBars]);

  // ---- 搜索（防抖） ----
  const onQuery = (v: string) => {
    setQuery(v);
    if (timer.current) window.clearTimeout(timer.current);
    if (!v.trim()) {
      setHits([]);
      setShowHits(false);
      return;
    }
    timer.current = window.setTimeout(() => {
      searchLabSymbols(v, 20)
        .then((list) => {
          setHits(list);
          setShowHits(true);
        })
        .catch(() => setHits([]));
    }, 250);
  };

  const pick = (s: LabSymbol) => {
    setSymbol(s.code);
    setQuery('');
    setHits([]);
    setShowHits(false);
    setBt(null);
  };

  // ---- 策略参数 ----
  const curStrategy = useMemo(() => strategies.find((s) => s.id === sid), [strategies, sid]);
  const onStrategy = (id: string) => {
    setSid(id);
    const s = strategies.find((x) => x.id === id);
    setParams(s ? Object.fromEntries(s.params.map((p) => [p.k, p.default])) : {});
    setBt(null);
  };

  const runBacktest = async () => {
    if (!sid || !symbol) return;
    setBtBusy(true);
    setErr('');
    try {
      const r = await runLabBacktest({ strategy: sid, symbol, adj: 'backward', params });
      setBt(r);
      // 成交点标注要落在同一价格序列上 → 切到后复权（回测口径）
      if (adj !== 'backward') setAdj('backward');
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setErr(msg || '回测失败（后端 PyneCore 运行时或数据不可用）');
      setBt(null);
    } finally {
      setBtBusy(false);
    }
  };

  // 净值曲线复用全站 EquityChart（visx）：单线、绝对金额口径
  const equityLines = useMemo<ChartLine[]>(
    () =>
      bt
        ? [
            {
              id: 'equity',
              label: '策略净值',
              color: '#1a1a1a',
              points: bt.equity.map((p) => ({ t: Date.parse(p.date), v: p.value })),
            },
          ]
        : [],
    [bt],
  );

  const st = bt?.stats ?? {};  const statCards: { label: string; value: string; tone?: 'up' | 'down' }[] = bt
    ? [
        {
          label: '策略净收益',
          value: `${fmtMoney(st['Net profit']?.value)} (${fmtPct(st['Net profit']?.pct)})`,
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
      ]
    : [];

  return (
    <div className="lab-page">
      <header className="lab-head">
        <h1>行情回测</h1>
        <div className="lab-search">
          <input
            value={query}
            placeholder="输入代码或名称：600309 / 万华化学"
            onChange={(e) => onQuery(e.target.value)}
            onFocus={() => hits.length && setShowHits(true)}
          />
          {showHits && hits.length > 0 && (
            <ul className="lab-hits">
              {hits.map((h) => (
                <li key={h.code} onMouseDown={() => pick(h)}>
                  <span className="code">{h.code}</span>
                  <span className="name">{h.name}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="lab-symbol">
          <strong>{stockName || symbol}</strong>
          <span className="code">{symbol}</span>
        </div>
        <div className="lab-adj">
          {ADJ_LABELS.map((a) => (
            <button
              key={a.id}
              className={adj === a.id ? 'on' : ''}
              onClick={() => setAdj(a.id)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </header>

      {err && <div className="lab-err">{err}</div>}

      <div className="lab-grid">
        <section className="lab-chart">
          {chartBusy && <div className="lab-mask">加载中…</div>}
          <KLineChart bars={bars} trades={bt?.trades ?? []} height={460} />
          <div className="lab-hint">
            quantdb 日线 · {bars.length} 根 · {bars[0]?.date ?? '—'} ~ {bars[bars.length - 1]?.date ?? '—'}
          </div>
        </section>

        <aside className="lab-panel">
          <div className="lab-panel-block">
            <div className="lab-block-title">策略</div>
            <select value={sid} onChange={(e) => onStrategy(e.target.value)}>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {curStrategy && <p className="lab-desc">{curStrategy.desc}</p>}
            <div className="lab-params">
              {curStrategy?.params.map((p) => (
                <label key={p.k}>
                  <span>{p.label}</span>
                  <input
                    type="number"
                    value={params[p.k] ?? p.default}
                    step="any"
                    onChange={(e) =>
                      setParams((prev) => ({ ...prev, [p.k]: Number(e.target.value) }))
                    }
                  />
                </label>
              ))}
            </div>
            <button className="lab-run" disabled={btBusy || !sid} onClick={runBacktest}>
              {btBusy ? '回测中…' : '运行回测（后复权 · 全历史）'}
            </button>
          </div>

          {bt && (
            <div className="lab-panel-block">
              <div className="lab-block-title">
                结果 · {bt.meta.name} @ {bt.meta.symbol}
              </div>
              <div className="lab-stats">
                {statCards.map((c) => (
                  <div key={c.label} className={`lab-stat ${c.tone ?? ''}`}>
                    <span className="k">{c.label}</span>
                    <span className="v">{c.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>

      {bt && (
        <div className="lab-bottom">
          <section className="lab-equity">
            <div className="lab-block-title">净值曲线（初始 ¥100,000）</div>
            <EquityChart lines={equityLines} currency="¥" mode="dollar" height={200} />
          </section>
          <section className="lab-trades">
            <div className="lab-block-title">
              逐笔成交（{bt.trades.length} 笔，显示最近 60 笔）
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
                  {bt.trades.slice(-60).reverse().map((t, i) => (
                    <tr key={`${t.entry_time}-${i}`} className={(t.profit ?? 0) >= 0 ? 'up' : 'down'}>
                      <td>{t.entry_time?.slice(0, 10)}</td>
                      <td>{fmt(t.entry_price)}</td>
                      <td>{t.exit_time?.slice(0, 10) ?? '持仓中'}</td>
                      <td>{fmt(t.exit_price)}</td>
                      <td>{t.profit === null ? '—' : `${fmtPct(t.profit_pct)}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
