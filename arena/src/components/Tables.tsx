import { ClosedTradeDetail, Holdings, PositionRecord, TradeRecord } from '../api/client';
import { fmtDate, fmtNum } from '../utils/format';

/** LAST 25 TRADES 平仓明细表（nof1 列结构：SIDE/COIN/ENTRY/EXIT/QTY/HOLDING/NOTIONAL ENTRY|EXIT/FEES/NET P&L）。 */
export function LastTradesTable({ trades, currency = '$' }: { trades: ClosedTradeDetail[]; currency?: string }) {
  const holdText = (days: number | null): string => {
    if (days == null) return '—';
    if (days < 1) return `${Math.round(days * 24)}H 0M`;
    return `${days}D 0H`;
  };
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>DATE</th>
            <th>SIDE</th>
            <th>COIN</th>
            <th>ENTRY PRICE</th>
            <th>EXIT PRICE</th>
            <th>QUANTITY</th>
            <th>HOLDING TIME</th>
            <th>NOTIONAL ENTRY</th>
            <th>NOTIONAL EXIT</th>
            <th>TOTAL FEES</th>
            <th>NET P&L</th>
          </tr>
        </thead>
        <tbody>
          {trades.length === 0 && (
            <tr><td colSpan={11} className="faint">暂无已平仓记录</td></tr>
          )}
          {trades.map((t, i) => {
            const entryNotional = t.qty * t.entry_price;
            return (
              <tr key={`${t.exit_date}-${t.symbol}-${i}`}>
                <td className="faint">{fmtDate(t.exit_date)}</td>
                <td className="up">LONG</td>
                <td><b>{t.symbol}</b></td>
                <td>{fmtNum(t.entry_price)}</td>
                <td>{fmtNum(t.exit_price)}</td>
                <td>{t.qty.toLocaleString('en-US')}</td>
                <td className="faint">{holdText(t.hold_days)}</td>
                <td>{fmtNum(entryNotional, 0)}</td>
                <td>{fmtNum(t.notional, 0)}</td>
                <td className="faint">{fmtNum(t.fee, 2)}</td>
                <td className={t.pnl >= 0 ? 'up' : 'down'}>
                  {t.pnl >= 0 ? '+' : ''}{currency}{Math.abs(t.pnl).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 持仓明细表：数量/成本/最新价/市值/浮动盈亏/占比（含现金行）。 */
export function HoldingsTable({ data, currency = '$' }: { data: Holdings | null; currency?: string }) {
  if (!data) return null;
  const rows = data.holdings;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>证券</th>
            <th>数量</th>
            <th>最新价</th>
            <th>成本价</th>
            <th>市值</th>
            <th>浮动盈亏</th>
            <th>盈亏率</th>
            <th>占比</th>
          </tr>
        </thead>
        <tbody>
          <tr className="faint">
            <td><b>CASH</b></td>
            <td colSpan={5}>{currency}{data.cash.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
            <td className="dim">—</td>
            <td className="dim">{data.total_equity ? fmtNum(data.cash / data.total_equity * 100, 1) + '%' : '—'}</td>
          </tr>
          {rows.length === 0 && (
            <tr><td colSpan={8} className="faint">空仓 — 无持仓</td></tr>
          )}
          {rows.map((h) => (
            <tr key={h.symbol}>
              <td><b>{h.symbol}</b></td>
              <td>{h.qty.toLocaleString('en-US')}</td>
              <td>{fmtNum(h.price)}</td>
              <td className="faint">{fmtNum(h.entry_price)}</td>
              <td>{fmtNum(h.market_value, 0)}</td>
              <td className={h.pnl >= 0 ? 'up' : 'down'}>{h.pnl >= 0 ? '+' : ''}{currency}{Math.abs(h.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
              <td className={h.pnl >= 0 ? 'up' : 'down'}>{h.pnl_pct != null ? (h.pnl_pct >= 0 ? '+' : '') + fmtNum(h.pnl_pct * 100, 2) + '%' : '—'}</td>
              <td className="dim">{h.weight_pct != null ? fmtNum(h.weight_pct * 100, 1) + '%' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="faint" style={{ marginTop: 6, fontSize: 11 }}>
        总权益 {currency}{data.total_equity.toLocaleString('en-US', { maximumFractionDigits: 0 })} = 现金 {currency}{data.cash.toLocaleString('en-US', { maximumFractionDigits: 0 })} + 持仓市值 {currency}{data.total_market_value.toLocaleString('en-US', { maximumFractionDigits: 0 })}
      </div>
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
