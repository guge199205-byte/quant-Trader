/** P2 Tab 集合：财务/估值/筹码资金/两融/情绪/股东分红（asof=日历点选日，随日期联动） */

import { useEffect, useState } from 'react';
import { message } from 'antd';
import { BarChart3, Coins, Landmark, BrainCircuit, Users2, PieChart } from 'lucide-react';
import { stockTerminalService, FinancialsResponse, SeriesResponse, DividendItem } from '../../services/stockTerminalService';
import { SeriesChart, buildSeries } from './SeriesChart';

export interface TabProps { symbol: string; asof?: string; }

const _divFmt = (v: number | null, digits = 3): string => v == null ? '--' : Number(v).toFixed(digits);
const _f2 = (v: number | null): string => v == null ? '--' : Number(v).toFixed(2);

function TabShell({ title, icon: Icon, children, loading }: {
  title: string; icon: any; children: React.ReactNode; loading?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 h-full">
      <div className="flex items-center gap-1.5">
        <div className="w-6 h-6 rounded-lg bg-blue-50 border border-blue-100 flex items-center justify-center text-blue-600">
          <Icon className="w-3.5 h-3.5" />
        </div>
        <span className="text-xs font-black text-slate-700">{title}</span>
        {loading && <span className="text-[10px] text-blue-400 animate-pulse">加载中…</span>}
      </div>
      {children}
    </div>
  );
}

/** 表格式记录渲染（倒序，columns=periods） */
function FinTable({ records, periods }: { records: { period: string; items: Record<string, number | null> }[]; periods: string[] }) {
  if (!records.length) return <div className="text-[11px] text-slate-400 py-4 text-center">暂无数据</div>;
  const rows = Object.keys(records[0]?.items ?? {});
  return (
    <div className="overflow-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-slate-400 font-bold">
            <th className="text-left py-1 pr-2 sticky left-0 bg-white/90">指标</th>
            {periods.map(p => <th key={p} className="text-right py-1 font-mono">{p}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r} className="border-t border-slate-50">
              <td className="py-1 pr-2 sticky left-0 bg-white/90 font-bold text-slate-600 whitespace-nowrap">{r}</td>
              {periods.map(p => {
                const rec = records.find(x => x.period === p);
                const v = rec?.items[r];
                return <td key={p} className="text-right py-1 font-mono text-slate-700">{v == null ? '--' : _f2(v)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- 财务报表 ----------
export function FinancialsTab({ symbol, asof }: TabProps) {
  const [data, setData] = useState<FinancialsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    stockTerminalService.getFinancials(symbol, 8, asof).then(d => { if (!c) setData(d); }).catch(() => message.error('财务数据加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  if (!data) return <TabShell title="财务报表" icon={BarChart3} loading={loading}><div /></TabShell>;
  return (
    <TabShell title="财务报表" icon={BarChart3} loading={loading}>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        {data.per_share?.[0] && (
          <>
            <div className="bg-white/70 rounded-xl border border-slate-100 p-2">
              <div className="text-[10px] text-slate-400 font-bold">ROE</div>
              <div className="text-base font-black text-blue-600">{_f2(data.per_share[0].items['ROE(%)'])}%</div>
            </div>
            <div className="bg-white/70 rounded-xl border border-slate-100 p-2">
              <div className="text-[10px] text-slate-400 font-bold">毛利率</div>
              <div className="text-base font-black text-emerald-600">{_f2(data.per_share[0].items['毛利率(%)'])}%</div>
            </div>
            <div className="bg-white/70 rounded-xl border border-slate-100 p-2">
              <div className="text-[10px] text-slate-400 font-bold">净利率</div>
              <div className="text-base font-black text-emerald-600">{_f2(data.per_share[0].items['净利率(%)'])}%</div>
            </div>
            <div className="bg-white/70 rounded-xl border border-slate-100 p-2">
              <div className="text-[10px] text-slate-400 font-bold">营收增速 / 净利增速</div>
              <div className="text-base font-black text-amber-600">
                {_f2(data.per_share[0].items['营收增速(%)'])}% / {_f2(data.per_share[0].items['净利增速(%)'])}%
              </div>
            </div>
          </>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 min-h-0">
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">利润表（亿元）</div>
          <FinTable records={data.income} periods={data.periods} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">每股指标</div>
          <FinTable records={data.per_share} periods={data.periods} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">资产负债表（亿元）</div>
          <FinTable records={data.balance} periods={data.periods} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">现金流量表（亿元）</div>
          <FinTable records={data.cashflow} periods={data.periods} />
        </div>
      </div>
    </TabShell>
  );
}

// ---------- 估值走势 ----------
export function ValuationTab({ symbol, asof }: TabProps) {
  const [resp, setResp] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    stockTerminalService.getSeries(symbol, 'valuation', 3, asof).then(r => { if (!c) setResp(r); }).catch(() => message.error('估值加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  const series = buildSeries(resp, [
    { key: 'pe_ttm', name: 'PE(TTM)', color: '#3b82f6' },
    { key: 'pb', name: 'PB', color: '#f59e0b' },
    { key: 'ps_ttm', name: 'PS(TTM)', color: '#8b5cf6' },
  ]);
  const div = buildSeries(resp, [
    { key: 'dividend_rate', name: '股息率(%)', color: '#10b981' },
  ]);
  return (
    <TabShell title="估值走势" icon={Coins} loading={loading}>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">PE / PB / PS</div>
          <SeriesChart resp={resp} series={series} height={200} tooltipFmt={(n, v) => `${n}: ${_f2(v)}`} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">股息率</div>
          <SeriesChart resp={resp} series={div} height={200} tooltipFmt={(n, v) => `${n}: ${_divFmt(v)}%`} />
        </div>
      </div>
    </TabShell>
  );
}

// ---------- 筹码资金 ----------
export function ChipFlowTab({ symbol, asof }: TabProps) {
  const [chip, setChip] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [flow, setFlow] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    Promise.all([
      stockTerminalService.getSeries(symbol, 'chip', 2, asof),
      stockTerminalService.getSeries(symbol, 'flow', 1, asof),
    ]).then(([ch, fl]) => { if (!c) { setChip(ch); setFlow(fl); } }).catch(() => message.error('筹码/资金加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  const chipS = buildSeries(chip, [
    { key: 'chip_profit_ratio_20', name: '20日获利盘(%)', color: '#f59e0b' },
    { key: 'chip_profit_ratio_60', name: '60日获利盘(%)', color: '#8b5cf6' },
    { key: 'chip_concentration_20', name: '筹码集中度', color: '#3b82f6' },
  ]);
  const flowS = buildSeries(flow, [
    { key: 'flow_net_amount', name: '主力净流入(元)', color: '#e11d48' },
    { key: 'flow_super_net', name: '超大单净流入(元)', color: '#f97316' },
  ]);
  return (
    <TabShell title="筹码与资金" icon={PieChart} loading={loading}>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">筹码分布（获利盘 / 集中度）</div>
          <SeriesChart resp={chip} series={chipS} height={220} tooltipFmt={(n, v) => `${n}: ${_divFmt(v)}`} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">主力资金流（元）</div>
          <SeriesChart resp={flow} series={flowS} height={220} tooltipFmt={(n, v) => `${n}: ${_divFmt(v, 0)}`} />
        </div>
      </div>
    </TabShell>
  );
}

// ---------- 融资融券 ----------
export function MarginTab({ symbol, asof }: TabProps) {
  const [resp, setResp] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    stockTerminalService.getSeries(symbol, 'margin', 3, asof).then(r => { if (!c) setResp(r); }).catch(() => message.error('两融加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  const series = buildSeries(resp, [
    { key: 'finance_balance', name: '融资余额(元)', color: '#3b82f6' },
    { key: 'finance_net', name: '融资净买入(元)', color: '#f59e0b' },
  ]);
  return (
    <TabShell title="融资融券" icon={Landmark} loading={loading}>
      <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
        <SeriesChart resp={resp} series={series} height={260} tooltipFmt={(n, v) => `${n}: ${_divFmt(v, 0)}`} />
      </div>
    </TabShell>
  );
}

// ---------- 技术形态 / 市场情绪 ----------
export function SentimentTab({ symbol, asof }: TabProps) {
  const [resp, setResp] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    Promise.all([
      stockTerminalService.getSeries(symbol, 'technical', 3, asof),
      stockTerminalService.getSeries(symbol, 'sentiment', 2, asof),
    ]).then(([t, s]) => {
      if (!c) {
        setResp({ dates: t.dates, columns: { ...t.columns, ...s.columns } });
      }
    }).catch(() => message.error('情绪加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  const tech = buildSeries(resp, [
    { key: 'rsi_14', name: 'RSI14', color: '#6366f1' },
    { key: 'macd_hist', name: 'MACD柱', color: '#e11d48' },
  ]);
  const senti = buildSeries(resp, [
    { key: 'buy_pressure', name: '买入压力', color: '#f59e0b' },
    { key: 'sell_pressure', name: '卖出压力', color: '#10b981' },
    { key: 'liquidity_score', name: '流动性评分', color: '#3b82f6' },
  ]);
  return (
    <TabShell title="技术形态与情绪" icon={BrainCircuit} loading={loading}>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">RSI / MACD</div>
          <SeriesChart resp={resp} series={tech} height={220} tooltipFmt={(n, v) => `${n}: ${_f2(v)}`} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">买卖压力 / 流动性</div>
          <SeriesChart resp={resp} series={senti} height={220} tooltipFmt={(n, v) => `${n}: ${_f2(v)}`} />
        </div>
      </div>
    </TabShell>
  );
}

// ---------- 股东户数 / 分红 ----------
export function HoldersTab({ symbol, asof }: TabProps) {
  const [hn, setHn] = useState<SeriesResponse>({ dates: [], columns: {} });
  const [divs, setDivs] = useState<DividendItem[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let c = false;
    setLoading(true);
    Promise.all([
      stockTerminalService.getSeries(symbol, 'holders', 3, asof),
      stockTerminalService.getDividends(symbol, asof),
    ]).then(([h, d]) => { if (!c) { setHn(h); setDivs(d); } }).catch(() => message.error('股东/分红加载失败')).finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  }, [symbol, asof]);
  const hS = buildSeries(hn, [{ key: 'holder_num', name: '股东户数', color: '#3b82f6' }]);
  return (
    <TabShell title="股东户数与分红" icon={Users2} loading={loading}>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2">
          <div className="text-[11px] font-bold text-slate-500 mb-1">股东户数（户数降=筹码集中）</div>
          <SeriesChart resp={hn} series={hS} height={200} tooltipFmt={(n, v) => `${n}: ${v == null ? '--' : Math.round(v).toLocaleString()}`} />
        </div>
        <div className="bg-white/70 rounded-2xl border border-slate-100 p-2 min-h-0">
          <div className="text-[11px] font-bold text-slate-500 mb-1">分红记录</div>
          <div className="h-[200px] overflow-auto">
            <table className="w-full text-[11px]">
              <thead className="text-slate-400 font-bold">
                <tr><th className="text-left py-1">除权日</th><th className="text-right">每股派息(元)</th><th className="text-right">送/转</th></tr>
              </thead>
              <tbody>
                {divs.slice(0, 20).map(d => (
                  <tr key={d.date} className="border-t border-slate-50">
                    <td className="py-1 font-mono">{d.date}</td>
                    <td className="text-right font-mono text-rose-500 font-bold">{d.interest ?? '--'}</td>
                    <td className="text-right font-mono">{d.stock_bonus || d.stock_gift ? `${d.stock_bonus ?? 0}/${d.stock_gift ?? 0}` : '--'}</td>
                  </tr>
                ))}
                {!divs.length && <tr><td colSpan={3} className="py-4 text-center text-slate-400">暂无分红记录</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </TabShell>
  );
}
