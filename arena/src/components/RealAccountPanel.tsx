import { useMemo } from 'react';
import dayjs from 'dayjs';
import {
  L2FactorRow,
  RealLedgerRow,
  fetchL2Factors,
  fetchRealAccount,
  fetchRealLedger,
} from '../api/client';
import { fmtMoney } from '../utils/format';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { ChartLine } from './EquityChart';

/** 实盘账户 tab：quantmind TDX 桥实盘账户（总资产/现金/持仓）+
 *  日终账本净值曲线 + L2 因子快照。20s 轮询，读 quantmind PG 只读同步。 */
export default function RealAccountPanel() {
  const acct = usePolling(() => fetchRealAccount(), [], 20000);
  const ledger = usePolling(() => fetchRealLedger(), [], 30000);
  const l2 = usePolling(() => fetchL2Factors(200), [], 20000);

  // 账本 → 净值曲线（绝对净值，组件内归一化）
  const ledgerLine: ChartLine[] = useMemo(() => {
    const rows: RealLedgerRow[] = ledger.data ?? [];
    return [
      {
        id: 'real-ledger',
        label: '实盘总资产',
        color: '#111',
        points: rows
          .filter((r) => r.total_asset > 0)
          .map((r) => ({ t: dayjs(r.date).valueOf(), v: r.total_asset })),
      },
    ];
  }, [ledger.data]);

  // L2 因子：按 symbol 取最近一条
  const l2Latest = useMemo(() => {
    const map = new Map<string, L2FactorRow>();
    for (const r of l2.data ?? []) {
      if (!map.has(r.symbol)) map.set(r.symbol, r);
    }
    return [...map.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [l2.data]);

  const acc = acct.data;
  const lastLedger = (ledger.data ?? []).length
    ? (ledger.data as RealLedgerRow[])[(ledger.data as RealLedgerRow[]).length - 1]
    : null;
  const dailyReturn = lastLedger?.daily_return_pct ?? null;

  return (
    <div className="real-body">
      {/* 账户卡 */}
      <div className="real-account-card">
        <div className="real-account-head">
          <span className="real-account-title">实盘账户（通达信桥）</span>
          {acc?.ts && <span className="real-account-ts">更新 {acc.ts.slice(5, 16)}</span>}
        </div>
        {acc ? (
          <>
            <div className="real-asset-row">
              <span className="real-asset-label">总资产</span>
              <span className="real-asset-value">{fmtMoney(acc.total_asset, '¥')}</span>
            </div>
            <div className="real-sub-row">
              <span>现金 {fmtMoney(acc.cash, '¥')}</span>
              <span>市值 {fmtMoney(acc.market_value, '¥')}</span>
              <span className={dailyReturn !== null && dailyReturn >= 0 ? 'real-up' : 'real-down'}>
                日收益{' '}
                {dailyReturn !== null ? `${dailyReturn >= 0 ? '+' : ''}${dailyReturn.toFixed(2)}%` : '—'}
              </span>
            </div>
            {(acc.positions ?? []).length > 0 && (
              <div className="real-positions">
                {acc.positions.map((p) => (
                  <div className="real-pos-row" key={p.symbol}>
                    <span className="real-pos-name">{p.name || p.symbol}</span>
                    <span className="real-pos-code">{p.symbol}</span>
                    <span className="real-pos-qty">{Number(p.volume).toLocaleString('en-US')}</span>
                    <span className="real-pos-val">{fmtMoney(Number(p.market_value), '¥')}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="empty-state" style={{ padding: '14px 0' }}>
            {acct.error ? '实盘账户读取失败（quantmind PG 未连接）' : '加载中…'}
          </div>
        )}
      </div>

      {/* 日终账本曲线 */}
      <div className="real-section">
        <div className="real-section-title">日终账本（{ledger.data?.length ?? 0} 个交易日）</div>
        {(ledger.data ?? []).length >= 2 ? (
          <div className="real-chart">
            <EquityChart lines={ledgerLine} benchmark={null} currency="¥" mode="pct" timeRange="all" />
          </div>
        ) : (
          <div className="empty-state" style={{ padding: '18px 0' }}>账本数据不足</div>
        )}
      </div>

      {/* L2 因子 */}
      <div className="real-section">
        <div className="real-section-title">
          L2 因子快照
          {l2.data?.length ? (
            <span className="real-section-sub">{l2.data[0].ts.slice(5, 16)} 最新</span>
          ) : null}
        </div>
        {l2Latest.length === 0 ? (
          <div className="empty-state" style={{ padding: '18px 0' }}>
            {l2.error ? 'L2 读取失败' : '暂无 L2 因子'}
          </div>
        ) : (
          <table className="real-l2-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>价</th>
                <th>VPIN</th>
                <th>分区</th>
                <th>价量背离</th>
                <th>冲击半衰</th>
              </tr>
            </thead>
            <tbody>
              {l2Latest.map((r) => (
                <tr key={r.symbol}>
                  <td className="real-l2-code">{r.stock_code}</td>
                  <td>{r.now_price ?? '—'}</td>
                  <td>{fmtFactor(r.factors['micro_vpin_vol_ratio'])}</td>
                  <td>{fmtFactor(r.factors['micro_zone_distribution'])}</td>
                  <td>{fmtFactor(r.factors['vol_price_divergence'])}</td>
                  <td>{fmtFactor(r.factors['micro_impact_decay_half_life'])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/** 因子值格式化：null → '—'，否则保留 3 位 */
const fmtFactor = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : Number(v).toFixed(3);
