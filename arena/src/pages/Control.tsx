import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MarketId,
  SERVICE_NAMES,
  fetchMetrics,
  fetchOverview,
  marketMeta,
} from '../api/client';
import { usePolling } from '../hooks/usePolling';
import { fmtDate, fmtMoney, fmtPct, pnlClass } from '../utils/format';
import './Control.css';

const MARKET_FLAG: Record<MarketId, string> = { us: '🇺🇸', cn: '🇨🇳', hk: '🇭🇰' };

/** 总控台 —— 参考 nof0 monitor.html：服务健康条 + 三市场汇总表 + 最近交易时间 */
export default function Control() {
  const nav = useNavigate();

  const metrics = usePolling(() => fetchMetrics(), [], 30000);
  const overview = usePolling(() => fetchOverview(), [], 30000);

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

      {/* 三市场区块 */}
      {(['us', 'cn', 'hk'] as MarketId[]).map((m) => {
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
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>当前权益</th>
                      <th>收益率</th>
                      <th>最大回撤</th>
                      <th>记录数</th>
                      <th>最新日期</th>
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
  );
}
