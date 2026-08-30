import { PositionRecord } from '../api/client';
import { fmtDate } from '../utils/format';

/** 持仓/成交记录表：{date, this_action, positions}。
 *  positions: {CASH: number, SYMBOL: qty}；this_action 存在时说明该行有交易动作。 */
export function PositionsTable({ records, currency = '$' }: { records: PositionRecord[]; currency?: string }) {
  const last = records[records.length - 1];
  const rows = last ? Object.entries(last.positions) : [];

  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>日期</th>
            <th>证券</th>
            <th>数量</th>
            <th>现金</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={5} className="faint">无持仓（空仓）</td></tr>
          )}
          {rows.map(([sym, qty]) => (
            <tr key={sym}>
              <td>{fmtDate(last?.date)}</td>
              <td>{sym === 'CASH' ? <span className="accent">CASH</span> : sym}</td>
              <td>{qty === 0 ? '—' : Number(qty).toLocaleString('en-US')}</td>
              <td>{sym === 'CASH' ? `${currency}${Number(qty).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}</td>
              <td className="faint">{last?.this_action ? '—' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 交易明细表：只有带 this_action 的行，展示买卖方向与数量 */
export function TradesTable({ records, currency = '$' }: { records: PositionRecord[]; currency?: string }) {
  const trades = records.filter((r) => r.this_action);
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>日期</th>
            <th>方向</th>
            <th>证券</th>
            <th>数量</th>
            <th>现金</th>
          </tr>
        </thead>
        <tbody>
          {trades.length === 0 && (
            <tr><td colSpan={5} className="faint">暂无成交记录</td></tr>
          )}
          {trades.map((t, i) => {
            const a = t.this_action!;
            const buy = a.action === 'buy';
            return (
              <tr key={`${t.date}-${i}`}>
                <td>{fmtDate(t.date)}</td>
                <td className={buy ? 'up' : 'down'}>{buy ? '▲ BUY' : '▼ SELL'}</td>
                <td>{a.symbol}</td>
                <td>{a.amount}</td>
                <td className="faint">{currency}{Number(t.positions?.CASH ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
