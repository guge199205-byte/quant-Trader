import { useNavigate } from 'react-router-dom';
import { EquityPoint, MarketId, marketMeta, Summary } from '../api/client';
import { fmtDate, fmtMoney, fmtNum, fmtPct, pnlClass } from '../utils/format';
import Sparkline from './Sparkline';

/** 模型卡：名称 + 市场 + 净值/收益 + 指标 + 迷你曲线。点击进 ModelDetail。 */
export default function ModelCard({
  market,
  agent,
  equitySeries,
  summary,
}: {
  market: MarketId;
  agent: string;
  equitySeries: EquityPoint[];
  summary: Summary | null;
}) {
  const nav = useNavigate();
  const meta = marketMeta(market);
  const points = equitySeries.length > 30 ? equitySeries.slice(-60) : equitySeries;
  const latestDate = points.length ? points[points.length - 1].date : null;
  const ret = summary?.total_return ?? null;

  return (
    <div className="model-card" onClick={() => nav(`/model/${market}/${encodeURIComponent(agent)}`)}>
      <div className="head">
        <span className="name">{agent}</span>
        <span className="market-tag">{meta.label}</span>
      </div>
      <Sparkline points={points} width={260} height={34} />
      <div className="equity">{fmtMoney(summary?.end_equity ?? null, meta.currency)}</div>
      <div className={`ret ${pnlClass(ret)}`}>{fmtPct(ret)}</div>
      <div className="meta">
        <span>SHARPE<b>{fmtNum(summary?.sharpe)}</b></span>
        <span>MAX DD<b>{fmtPct(summary?.max_drawdown, 2, false)}</b></span>
        <span>胜率<b>{summary?.win_rate != null ? fmtPct(summary.win_rate, 1, false) : '—'}</b></span>
        <span>平仓<b>{summary?.closed_trades ?? 0}</b></span>
        <span>记录<b>{summary?.records ?? 0}</b></span>
        <span>最新<b>{fmtDate(latestDate)}</b></span>
      </div>
    </div>
  );
}
