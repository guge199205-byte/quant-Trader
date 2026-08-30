import { ClosedTradeDetail, PositionRecord, TradeRecord } from '../api/client';
import { fmtDate, fmtNum } from '../utils/format';

/** LAST 25 TRADES 平仓明细表：FIFO 重建逐笔（entry/exit/hold/notional/fee/pnl）。 */
export function LastTradesTable({ trades, currency = '$' }: { trades: ClosedTradeDetail[]; currency?: string }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>方向</th>
            <th>证券</th>
            <th>平仓日</th>
            <th>买入价</th>
            <th>卖出价</th>
            <th>数量</th>
            <th>持仓</th>
            <th>名义</th>
            <th>费用</th>
            <th>净盈亏</th>
          </tr>
        </thead>
        <tbody>
          {trades.length === 0 && (
            <tr><td colSpan={10} className="faint">暂无已平仓记录</td></tr>
          )}
          {trades.map((t, i) => (
            <tr key={`${t.exit_date}-${t.symbol}-${i}`}>
              <td className="up">LONG</td>
              <td>{t.symbol}</td>
              <td className="faint">{fmtDate(t.exit_date)}</td>
              <td>{fmtNum(t.entry_price)}</td>
              <td>{fmtNum(t.exit_price)}</td>
              <td>{t.qty.toLocaleString('en-US')}</td>
              <td className="faint">{t.hold_days != null ? `${t.hold_days}d` : '—'}</td>
              <td>{fmtNum(t.notional, 0)}</td>
              <td className="faint">{fmtNum(t.fee, 2)}</td>
              <td className={t.pnl >= 0 ? 'up' : 'down'}>
                {t.pnl >= 0 ? '+' : ''}{currency}{Math.abs(t.pnl).toLocaleString('en-US', { maximumFractionDigits: 2 })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
