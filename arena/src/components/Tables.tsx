import { PositionRecord, TradeRecord } from '../api/client';
import { fmtDate } from '../utils/format';

/** 持仓记录表：{date, positions}；positions: {CASH: number, SYMBOL: qty}。 */
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
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={4} className="faint">无持仓（空仓）</td></tr>
          )}
          {rows.map(([sym, qty]) => (
            <tr key={sym}>
              <td>{fmtDate(last?.date)}</td>
              <td>{sym === 'CASH' ? <span className="accent">CASH</span> : sym}</td>
              <td>{qty === 0 ? '—' : Number(qty).toLocaleString('en-US')}</td>
              <td>{sym === 'CASH' ? `${currency}${Number(qty).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 交易明细表：/trades 顶层字段 {date, action, symbol, amount, cash_after} */
export function TradesTable({ records, currency = '$' }: { records: TradeRecord[]; currency?: string }) {
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
          {records.length === 0 && (
            <tr><td colSpan={5} className="faint">暂无成交记录</td></tr>
          )}
          {records.map((t, i) => {
            const buy = t.action === 'buy';
            return (
              <tr key={`${t.date}-${i}`}>
                <td>{fmtDate(t.date)}</td>
                <td className={buy ? 'up' : 'down'}>{buy ? '▲ BUY' : '▼ SELL'}</td>
                <td>{t.symbol}</td>
                <td>{t.amount}</td>
                <td className="faint">{currency}{Number(t.cash_after ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
