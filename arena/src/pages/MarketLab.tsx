/** 行情回测：quantdb 日线看盘 / 单标的回测 / 批量回测（策略排行 + 单策略选股）。
 *
 * 数据：quantdb（容器 /data/quantdb 只读挂载），全市场日线，三种复权口径。
 * 回测：PyneCore 运行时 + backend/services/lab_strategies/*.py 六个模板。
 * 批量结果：scripts/lab_batch_backtest.py 跑出的 data/lab_batch/*.json（页面只读）。
 */
import { useState } from 'react';
import BatchPanel, { type LabView } from '../components/lab/BatchPanel';
import SingleBacktest from '../components/lab/SingleBacktest';
import './MarketLab.css';

type Tab = 'single' | LabView;

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'single', label: '单标的回测', hint: 'K线 + 逐笔 + 净值' },
  { id: 'rank', label: '策略排行', hint: '一个池子，哪个策略最强' },
  { id: 'screener', label: '单策略选股', hint: '一个策略，该买哪几只' },
];

export default function MarketLab() {
  const [tab, setTab] = useState<Tab>('single');
  const [symbol, setSymbol] = useState('600309.SH');

  // 选股页点一行 → 跳到单标的回测看这只票
  const pickSymbol = (code: string) => {
    setSymbol(code);
    setTab('single');
  };

  return (
    <div className="lab-page">
      <header className="lab-head">
        <h1>行情回测</h1>
        <nav className="lab-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? 'on' : ''}
              onClick={() => setTab(t.id)}
              title={t.hint}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {tab === 'single' ? (
        <SingleBacktest symbol={symbol} onSymbol={setSymbol} />
      ) : (
        <BatchPanel view={tab} onPickSymbol={pickSymbol} />
      )}
    </div>
  );
}
