import { useMemo } from 'react';
import dayjs from 'dayjs';
import {
  L2FactorRow,
  LiveAccount,
  MarketId,
  RealLedgerRow,
  fetchL2Factors,
  fetchRealAccount,
  fetchRealLedger,
} from '../api/client';
import { fmtMoney } from '../utils/format';
import { usePolling } from '../hooks/usePolling';
import EquityChart, { ChartLine } from './EquityChart';

/** 实盘账户 tab。
 *  A股：quantmind TDX 桥实盘账户 + 日终账本净值曲线 + L2 因子快照。
 *  港股：富途实盘+模拟两套账户（持仓/资金/总资产），数据由 Live.tsx 挂在 15s
 *  后台轮询（fetchFutuAccountBoth，一次握手游走 REAL+SIMULATE），点击 tab 即见，
 *  不在本组件内单独起 futu 子进程（省一次 ~4s RSA 握手）。账本与 L2 为 A 股专属。 */
export default function RealAccountPanel({
  market,
  currency = '¥',
  futuBoth = null,
}: {
  market: MarketId;
  currency?: string;
  futuBoth?: { real: LiveAccount | null; simulate: LiveAccount | null } | null;
}) {
  const isHk = market === 'hk';
  const futuReal = futuBoth?.real ?? null;
  const futuSim = futuBoth?.simulate ?? null;
  // A股走 quantmind PG TDX 桥快照
  const acct = usePolling(() => fetchRealAccount(), [], 20000);
  const ledger = usePolling(() => fetchRealLedger(), [], 30000);
  const l2 = usePolling(() => fetchL2Factors(200), [], 20000);

  const acc = acct.data;
  const ledgerRows = (ledger.data ?? []) as RealLedgerRow[];
  const lastLedger = ledgerRows.length ? ledgerRows[ledgerRows.length - 1] : null;
  const dailyReturn = lastLedger?.daily_return_pct ?? null;
  // 过滤非交易日（周六日）账本行：TDX 桥周末也同步快照产生账本行，净值无变化，画在曲线上是平段
  const tradingRows = ledgerRows.filter((r) => {
    const d = dayjs(r.date).day();
    return d !== 0 && d !== 6;
  });

  // 账本 → 净值曲线（绝对净值，组件内归一化；仅交易日行）
  const ledgerLine: ChartLine[] = useMemo(() => {
    if (isHk) return [];
    const rows = tradingRows.filter((r) => r.total_asset > 0);
    return [
      {
        id: 'real-ledger',
        label: '实盘总资产',
        color: '#111',
        points: rows.map((r) => ({ t: dayjs(r.date).valueOf(), v: r.total_asset })),
      },
    ];
  }, [tradingRows, isHk]);

  // L2 因子：按 symbol 取最近一条
  const l2Latest = useMemo(() => {
    if (isHk) return [];
    const map = new Map<string, L2FactorRow>();
    for (const r of l2.data ?? []) {
      if (!map.has(r.symbol)) map.set(r.symbol, r);
    }
    return [...map.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [l2.data, isHk]);

  // ---------- 港股：富途实盘 + 模拟 两套账户卡 ----------
  if (isHk) {
    const renderFutuCard = (
      label: string,
      acctState: { data: LiveAccount | null; error?: string | null },
    ) => {
      const fa = acctState.data;
      const positions = (fa?.positions ?? []).filter((p) => Number(p.total_volume) > 0);
      const totalPnl = positions.reduce((s, p) => s + Number(p.pnl ?? 0), 0);
      return (
        <div className="real-account-card">
          <div className="real-account-head">
            <span className="real-account-title">{label}</span>
            <span className="real-account-ts">{fa ? `通道 ${fa.channel_used ?? 'futu'}` : '加载中…'}</span>
          </div>
          {fa ? (
            <>
              <div className="real-asset-row">
                <span className="real-asset-label">总资产</span>
                <span className="real-asset-value">{fmtMoney(fa.asset, currency)}</span>
              </div>
              <div className="real-sub-row">
                <span>总浮盈 {totalPnl >= 0 ? '+' : ''}{fmtMoney(totalPnl, currency)}</span>
                <span>持仓 {positions.length} 只</span>
              </div>
              {positions.length > 0 && (
                <div className="real-positions">
                  {positions.map((p) => (
                    <div className="real-pos-row" key={p.stock_code}>
                      <span className="real-pos-id">
                        <span className="real-pos-name">{p.name || p.stock_code}</span>
                        <span className="real-pos-code">{p.stock_code}</span>
                      </span>
                      <span className="real-pos-qty">{Number(p.total_volume).toLocaleString('en-US')}</span>
                      <span className={`real-pos-val ${Number(p.pnl) >= 0 ? 'real-up' : 'real-down'}`}>
                        {Number(p.pnl) >= 0 ? '+' : ''}{fmtMoney(Number(p.pnl), currency)}
                        <span className="real-pos-pct"> {Number(p.pnl_pct) >= 0 ? '+' : ''}{Number(p.pnl_pct).toFixed(2)}%</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="empty-state" style={{ padding: '14px 0' }}>
              {acctState.error ? '富途账户读取失败（OpenD 未连接或未登录）' : '加载中…'}
            </div>
          )}
        </div>
      );
    };
    return (
      <div className="real-body">
        {renderFutuCard('实盘账户（富途实盘）', { data: futuReal })}
        {renderFutuCard('模拟账户（富途模拟）', { data: futuSim })}
        <div className="real-section">
          <div className="real-section-title">日终账本</div>
          <div className="empty-state" style={{ padding: '18px 0' }}>港股账本曲线待接入（富途历史净值）</div>
        </div>
      </div>
    );
  }

  // ---------- A股：TDX 桥账户 + 账本 + L2 ----------
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
                    <span className="real-pos-id">
                      <span className="real-pos-name">{p.name || p.symbol}</span>
                      <span className="real-pos-code">{p.symbol}</span>
                    </span>
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
        <div className="real-section-title">日终账本（{tradingRows.length} 个交易日）</div>
        {tradingRows.length >= 2 ? (
          <div className="real-chart">
            {/* 必须显式传 height：默认 380 会溢出 210px 容器压住下方 L2 表格 */}
            <EquityChart lines={ledgerLine} benchmark={null} currency="¥" mode="pct" timeRange="all" height={180} />
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
                <th>名称</th>
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
                  <td className="real-l2-name">{r.name || r.stock_code}</td>
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
