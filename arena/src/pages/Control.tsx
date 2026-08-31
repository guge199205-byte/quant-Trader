import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AgentLedger,
  MarketId,
  SERVICE_NAMES,
  api,
  fetchLiveAccount,
  fetchLiveEquity,
  fetchLiveLedger,
  fetchMetrics,
  fetchOverview,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { fmtDate, fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Control.css';

const MARKET_FLAG: Record<MarketId, string> = { us: '🇺🇸', cn: '🇨🇳', hk: '🇭🇰' };

// ---------- 交易所状态（经 /api/quantmind 代理 → quantmind 8000） ----------

interface TdxStatus {
  enabled: boolean;
  bridge_url: string;
  bridge_token_configured: boolean;
  real_trading_enabled: boolean;
  health: { error?: string; tdx_connected?: boolean } | null;
}

interface BrokerStatus {
  broker: string;
  label: string;
  fields: Record<string, string | boolean>;
  loaded: boolean;
}

interface TradingStatus {
  status: string;
  mode: string;
}

async function fetchExchangeStatus() {
  const [tdx, tiger, futu, ib, rt] = await Promise.all([
    api.get('/quantmind/tdx/config').then((r) => r.data as TdxStatus).catch(() => null),
    api.get('/quantmind/broker-config/tiger').then((r) => r.data as BrokerStatus).catch(() => null),
    api.get('/quantmind/broker-config/futu').then((r) => r.data as BrokerStatus).catch(() => null),
    api.get('/quantmind/broker-config/ib').then((r) => r.data as BrokerStatus).catch(() => null),
    api.get('/quantmind/real-trading/status').then((r) => r.data as TradingStatus).catch(() => null),
  ]);
  const brokers = [tiger, futu, ib]
    .filter((b): b is BrokerStatus => !!b)
    .map((b) => ({ broker: b.broker, label: b.label, fields: b.fields, loaded: true }));
  return { tdx, brokers, rt };
}

const brokerConfigured = (b: { fields: Record<string, string | boolean> }) =>
  Object.entries(b.fields).some(([k, v]) => k.endsWith('_configured') && v === true) ||
  Object.entries(b.fields).some(([k, v]) => !k.endsWith('_configured') && typeof v === 'string' && v.length > 0);

/** 总控台 —— 参考 nof0 monitor.html：服务健康条 + 三市场汇总表 + 最近交易时间 */
export default function Control() {
  const nav = useNavigate();

  const metrics = usePolling(() => fetchMetrics(), [], 30000);
  const overview = usePolling(() => fetchOverview(), [], 30000);
  const exchange = usePolling(fetchExchangeStatus, [], 30000);
  // 实盘分账(A股, 通达信桥): 总资产 + 每 agent ¥10 万虚拟子账户, 20 秒刷新
  const liveAcct = usePolling(() => fetchLiveAccount(), [], 20000);
  const liveLedger = usePolling(() => fetchLiveLedger(), [], 20000);
  const liveEquity = usePolling(() => fetchLiveEquity(), [], 20000);

  const ageText = useMemo(() => {
    const age = metrics.data?.latest_trade_age_sec;
    if (age == null) return '无交易记录';
    if (age < 60) return `${age} 秒前`;
    if (age < 3600) return `${Math.floor(age / 60)} 分钟前`;
    return `${Math.floor(age / 3600)} 小时前`;
  }, [metrics.data]);

  if (overview.error) {
    return <div className="error-box">API 连接失败：{overview.error}</div>;
  }

  return (
    <div className="page">
      <div className="control-header">
        <h1 className="control-title">总控台</h1>
        <span className="control-refresh">
          更新于 {new Date().toLocaleTimeString('zh-CN')} · 最近交易 {ageText} · 30 秒自动刷新
        </span>
      </div>

      {/* 服务健康条 */}
      <div className="svc-row">
        {Object.entries(metrics.data?.services ?? {}).length === 0 && (
          <span className="dim" style={{ fontSize: 11 }}>服务状态加载中…</span>
        )}
        {Object.entries(metrics.data?.services ?? {}).map(([k, v]) => (
          <span className="svc-chip" key={k}>
            <span className={`svc-dot ${v === 'up' ? 'svc-up' : 'svc-down'}`} />
            {SERVICE_NAMES[k] ?? k} {v === 'up' ? '正常' : '掉线'}
          </span>
        ))}
      </div>

      {/* 交易所状态 + A股实盘分账：两列并排 */}
      <div className="control-grid control-grid-2">
      <section className="mk-section" style={{ border: '2px solid #000', padding: '10px 14px' }}>
        <div className="mk-head" style={{ marginBottom: 8 }}>
          <span>🏦 交易所状态</span>
          <span className="mk-count">
            {exchange.data?.tdx
              ? `通达信桥 ${exchange.data.tdx.health?.error ? '不可达' : '在线'}`
              : '交易所数据加载中…'}
          </span>
        </div>
        {exchange.data && (
          <div className="exch-row">
            {exchange.data.tdx && (
              <span className={`svc-chip ${exchange.data.tdx.health?.error ? 'svc-down' : 'svc-up'}`}>
                <span className={`svc-dot ${exchange.data.tdx.health?.error ? 'svc-down' : 'svc-up'}`} />
                通达信桥
                <span className="exch-sub">
                  {exchange.data.tdx.health?.error ? '不可达' : '在线'}
                  {exchange.data.tdx.health?.tdx_connected ? '· 客户端已连' : ''}
                </span>
                <span className={`exch-sub ${exchange.data.tdx.real_trading_enabled ? 'exch-on' : ''}`}>
                  实盘{exchange.data.tdx.real_trading_enabled ? '开' : '关'}
                </span>
                <span className="exch-sub">推送{exchange.data.tdx.enabled ? '开' : '关'}</span>
              </span>
            )}
            {exchange.data.brokers.map((b) => (
              <span key={b.broker} className={`svc-chip ${brokerConfigured(b) ? 'svc-up' : 'svc-down'}`}>
                <span className={`svc-dot ${brokerConfigured(b) ? 'svc-up' : 'svc-down'}`} />
                {b.label}
                <span className="exch-sub">{brokerConfigured(b) ? '已配置' : '未配置'}</span>
              </span>
            ))}
            {exchange.data.rt && (
              <span className={`svc-chip ${exchange.data.rt.status === 'running' ? 'svc-up' : 'svc-down'}`}>
                <span className={`svc-dot ${exchange.data.rt.status === 'running' ? 'svc-up' : 'svc-down'}`} />
                实时交易
                <span className="exch-sub">
                  {exchange.data.rt.status === 'running' ? '运行中' : '未运行'}
                  · {exchange.data.rt.mode === 'REAL' ? '实盘' : exchange.data.rt.mode === 'SIMULATION' ? '模拟盘' : exchange.data.rt.mode}
                </span>
              </span>
            )}
          </div>
        )}
      </section>

      {/* A股实盘分账(通达信桥) —— 总账户持仓 + 每 agent ¥10 万虚拟子账户 */}
      {liveAcct.data && (
        <section className="mk-section">
          <div className="mk-head">
            <span>🇨🇳 A股实盘(通达信桥)</span>
            <span className="mk-count">
              总资产 {fmtMoney(liveAcct.data.asset, '¥', 0)}
              {' '}· {liveAcct.data.positions.length} 只持仓 · 实盘分账
            </span>
          </div>
          <div className="table-wrap mk-table">
            <table className="data">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>虚拟净值</th>
                  <th>收益率</th>
                  <th>额度已用</th>
                  <th>名下持仓</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(liveLedger.data?.agents ?? {}).map(([name, ag]: [string, AgentLedger]) => {
                  const pts = liveEquity.data?.agents?.[name] ?? [];
                  const nav = pts.length ? pts[pts.length - 1].value : null;
                  const ret = nav != null ? (nav / (ag.quota || 100000) - 1) * 100 : null;
                  const posCount = Object.keys(ag.positions ?? {}).length;
                  return (
                    <tr key={name}>
                      <td style={{ fontWeight: 700 }}>{name}</td>
                      <td className={ret != null ? pnlClass(ret) : 'dim'}>
                        {nav != null ? fmtMoney(nav, '¥', 0) : '—'}
                      </td>
                      <td className={ret != null ? pnlClass(ret) : 'dim'}>
                        {ret != null ? fmtPct(ret) : '—'}
                      </td>
                      <td className="dim">
                        ¥{Math.round(ag.used).toLocaleString('zh-CN')} / ¥{Math.round(ag.quota).toLocaleString('zh-CN')}
                      </td>
                      <td className="dim">{posCount > 0 ? `${posCount} 只` : '空仓'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
      </div>

      {/* 三市场区块：cn/hk/us 三列并排 */}
      <div className="control-grid control-grid-3">
      {(['cn', 'hk', 'us'] as MarketId[]).map((m) => {
        const rows = overview.data?.markets[m] ?? [];
        const running = rows.filter((r) => r.summary).length;
        const meta = marketMeta(m);
        return (
          <section className="mk-section" key={m}>
            <div className="mk-head">
              <span>{MARKET_FLAG[m]} {meta.name}</span>
              <span className="mk-count">{running}/{rows.length} 个已交易</span>
            </div>
            {rows.length === 0 ? (
              <div className="mk-empty">暂无 agent 数据</div>
            ) : (
              <div className="table-wrap mk-table">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>当前权益</th>
                      <th>收益率</th>
                      <th>最大回撤</th>
                      <th>记录数</th>
                      <th>回放截止</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const s = r.summary;
                      return (
                        <tr
                          key={r.name}
                          className="clickable"
                          onClick={() => s && nav(`/model/${m}/${encodeURIComponent(r.name)}`)}
                        >
                          <td style={{ fontWeight: 700 }}>{r.name}</td>
                          <td>{s ? fmtMoney(s.end_equity, meta.currency) : '—'}</td>
                          <td className={s ? pnlClass(s.total_return) : 'dim'}>
                            {s ? fmtPct(s.total_return) : '—'}
                          </td>
                          <td className="dim">{s ? fmtPct(s.max_drawdown, 2, false) : '—'}</td>
                          <td className="dim">{s ? s.records : r.records}</td>
                          <td className="dim">{fmtDate(s ? r.latest_date : r.latest_date)}</td>
                          <td>
                            <span className={`status-badge ${s ? 'active' : 'stopped'}`}>
                              {s ? '已交易' : '未交易'}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
      </div>
    </div>
  );
}
