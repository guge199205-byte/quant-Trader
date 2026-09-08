/** Pine 策略库：桌面 TradingView 语料（1001 个）按分类浏览 / 搜索 / 查看源码 / 编辑保存。
 *
 * 语料三种来源，优先级 edited > recrawl > desktop：
 *  - desktop：旧版爬虫产物，块缩进被拍平（TradingView 自己也编译不了），仅可读；
 *  - recrawl：修好爬虫后重爬的，缩进完整；
 *  - edited：本页保存的编辑（写 data/pine_library/edited/，不动桌面原文件）。
 * 索引由 scripts/pine_library_index.py 生成。
 *
 * 「转写并回测」：本机没有 Pine 解释器，让 .pine 跑起来只能让模型翻成 Pyne-Python。
 * 页面只把请求写进队列（data/pine_transpile/queue/），宿主 worker
 * （scripts/pine_transpile_worker.py）转写 + 静态闸 + bwrap 沙箱回测，结果写回
 * <id>/job.json 与 report.json，这里轮询展示。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchPineList,
  fetchPineJob,
  fetchPineSource,
  fetchPineTranspile,
  resetPineSource,
  runPineBacktest,
  savePineSource,
  type PineJob,
  type PineListItem,
  type PineList,
  type PineReport,
  type PineTranspile,
} from '../../api/client';

const KIND_LABEL: Record<string, string> = {
  desktop: '原始语料',
  recrawl: '重爬（缩进完整）',
  edited: '已编辑',
  missing: '缺失',
};

const PAGE = 200;
const POLL_MS = 2500;

const fmtPct = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;

const num = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : v.toFixed(digits);

/** axios 错误 → 人话（后端 400 的 detail 优先） */
const errText = (e: unknown): string => {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (e instanceof Error ? e.message : String(e));
};

const STAGE_LABEL: Record<string, string> = {
  queued: '已入队，等宿主 worker 取（每分钟一轮）',
  transpile: '转写中：调模型翻成 Pyne-Python…',
  static: '静态检查未通过',
  backtest: '沙箱回测中（bwrap：只读根 + 断网）…',
  done: '完成',
  worker: 'worker 异常',
};

function Spark({ points }: { points: { date: string; value: number }[] }) {
  if (points.length < 2) return null;
  const vs = points.map((p) => p.value);
  const lo = Math.min(...vs);
  const hi = Math.max(...vs);
  const span = hi - lo || 1;
  const w = 100;
  const h = 30;
  const d = vs
    .map((v, i) => `${i ? 'L' : 'M'}${((i / (vs.length - 1)) * w).toFixed(2)},${(h - ((v - lo) / span) * h).toFixed(2)}`)
    .join('');
  return (
    <svg className="lab-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path
        d={d}
        fill="none"
        stroke={vs[vs.length - 1] >= vs[0] ? '#e2373b' : '#1a9e5c'}
        strokeWidth="1.2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export default function StrategyLibrary() {
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [list, setList] = useState<PineList | null>(null);
  const [listErr, setListErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [transpile, setTranspile] = useState<Record<string, PineTranspile>>({});

  const [cur, setCur] = useState<PineListItem | null>(null);
  const [draft, setDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [saveErr, setSaveErr] = useState('');
  const [saving, setSaving] = useState(false);
  const seq = useRef(0);

  const [symbol, setSymbol] = useState('600309.SH');
  const [job, setJob] = useState<PineJob | null>(null);
  const [report, setReport] = useState<PineReport | null>(null);
  const [queuing, setQueuing] = useState(false);
  const [btErr, setBtErr] = useState('');

  const load = useCallback(
    (cat: string, query: string, offset = 0) => {
      setBusy(true);
      setListErr('');
      const mySeq = ++seq.current;
      fetchPineList(cat, query, PAGE, offset)
        .then((d) => {
          if (mySeq !== seq.current) return;
          setList((prev) =>
            offset > 0 && prev
              ? { ...d, items: [...prev.items, ...d.items] }
              : d,
          );
        })
        .catch(() => {
          if (mySeq !== seq.current) return;
          setListErr('策略库读取失败（索引还没生成？跑 scripts/pine_library_index.py）');
        })
        .finally(() => {
          if (mySeq === seq.current) setBusy(false);
        });
    },
    [],
  );

  // 搜索防抖；分类切换立即生效
  useEffect(() => {
    const t = setTimeout(() => load(category, q), q ? 300 : 0);
    return () => clearTimeout(t);
  }, [category, q, load]);

  const refreshTranspile = useCallback(() => {
    fetchPineTranspile()
      .then(setTranspile)
      .catch(() => setTranspile({}));
  }, []);

  useEffect(() => {
    refreshTranspile();
  }, [refreshTranspile]);

  const loadJob = useCallback((id: string) => {
    fetchPineJob(id)
      .then((d) => {
        setJob(d.job);
        setReport(d.report);
      })
      .catch(() => undefined);
  }, []);

  const open = (id: string) => {
    setSaveMsg('');
    setSaveErr('');
    setBtErr('');
    setJob(null);
    setReport(null);
    fetchPineSource(id)
      .then((d) => {
        setCur(d);
        setDraft(d.source ?? '');
        setDirty(false);
      })
      .catch(() => setSaveErr('源码读取失败'));
    loadJob(id);
  };

  // 任务在跑就轮询；跑完刷新列表上的 AI 角标
  const running = job?.status === 'queued' || job?.status === 'running';
  useEffect(() => {
    if (!cur || !running) return;
    const t = setInterval(() => loadJob(cur.id), POLL_MS);
    return () => clearInterval(t);
  }, [cur, running, loadJob]);

  useEffect(() => {
    if (job?.status === 'done' || job?.status === 'failed') refreshTranspile();
  }, [job?.status, refreshTranspile]);

  const save = () => {
    if (!cur) return;
    setSaving(true);
    setSaveErr('');
    setSaveMsg('');
    savePineSource(cur.id, draft)
      .then((r) => {
        setDirty(false);
        setSaveMsg(`已保存 · ${r.lines} 行 · ${r.indent_ok ? '缩进完整' : '仍无缩进'}`);
        setCur({ ...cur, edited: true, source_kind: 'edited', source: draft });
      })
      .catch((e) => setSaveErr(errText(e) || '保存失败'))
      .finally(() => setSaving(false));
  };

  const reset = () => {
    if (!cur) return;
    setSaveErr('');
    resetPineSource(cur.id)
      .then(() => {
        setSaveMsg('已丢弃编辑，重新载入索引版本');
        open(cur.id);
      })
      .catch(() => setSaveErr('重置失败'));
  };

  const run = () => {
    if (!cur) return;
    setBtErr('');
    setQueuing(true);
    runPineBacktest(cur.id, symbol)
      .then(() => {
        setJob({ status: 'queued', stage: 'queued' });
        setReport(null);
      })
      .catch((e) => setBtErr(errText(e) || '入队失败'))
      .finally(() => setQueuing(false));
  };

  const items = list?.items ?? [];
  const canMore = list ? list.filtered > items.length : false;
  const tp = cur ? transpile[cur.id] : undefined;
  const st = report?.stats;
  const total = st?.['Total trades']?.value;
  const wins = st?.['Winning trades']?.value;
  const rows = (report?.trade_rows ?? []).slice(-8).reverse();

  return (
    <div className="lab-lib">
      <div className="lab-batch-bar">
        <label>
          <span>分类</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">全部（{list?.total ?? 0}）</option>
            {(list?.categories ?? []).map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}（{c.count}）
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>搜索</span>
          <input
            className="lab-lib-search"
            value={q}
            placeholder="标题 / 文件名 / 序号"
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <div className="lab-batch-meta">
          {list && (
            <>
              <span>命中 {list.filtered}</span>
              <span>缩进完整 {list.usable}</span>
              {list.generated && <span>索引 {list.generated.slice(0, 16).replace('T', ' ')}</span>}
            </>
          )}
        </div>
      </div>

      {listErr && <div className="lab-err">{listErr}</div>}

      <div className="lab-lib-grid">
        <div className="lab-lib-list">
          <table className="lab-rank">
            <thead>
              <tr>
                <th>#</th>
                <th>策略</th>
                <th>分类</th>
                <th className="num">版本</th>
                <th className="num">语法</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.id}
                  className={cur?.id === it.id ? 'on' : ''}
                  title={`${it.file}\n${it.lines} 行 · ${it.url}`}
                  onClick={() => open(it.id)}
                >
                  <td className="idx">{it.id}</td>
                  <td className="name">
                    {it.title || it.file}
                    {transpile[it.id] && (
                      <span className="badge-ai" title="已 AI 转写为 Pyne-Python">
                        AI
                      </span>
                    )}
                  </td>
                  <td className="cat">{it.category}</td>
                  <td className="num">{it.version ? `v${it.version}` : '—'}</td>
                  <td className="num">
                    <span className={it.indent_ok ? 'dot ok' : 'dot bad'} />
                    {it.source_kind === 'edited' ? '改' : it.indent_ok ? '可编译' : '缩进缺失'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {canMore && (
            <button className="lab-more" disabled={busy} onClick={() => load(category, q, items.length)}>
              {busy ? '加载中…' : `加载更多（还有 ${list!.filtered - items.length} 条）`}
            </button>
          )}
          {!busy && !items.length && !listErr && <div className="lab-empty">没有匹配的策略</div>}
        </div>

        <div className="lab-lib-src">
          {!cur ? (
            <div className="lab-empty">左侧点一个策略 → 这里看源码 / 回测</div>
          ) : (
            <>
              <div className="lab-lib-head">
                <div className="lab-lib-title">
                  <strong>{cur.title || cur.file}</strong>
                  <span className="code">{cur.id}</span>
                </div>
                <div className="lab-lib-tags">
                  <span className="tag">{cur.category}</span>
                  <span className="tag">{cur.version ? `Pine v${cur.version}` : '版本未知'}</span>
                  <span className="tag">{cur.lines} 行</span>
                  <span className={`tag ${cur.indent_ok ? '' : 'warn'}`}>
                    {KIND_LABEL[cur.source_kind] ?? cur.source_kind}
                  </span>
                  {cur.url && (
                    <a className="tag link" href={cur.url} target="_blank" rel="noreferrer">
                      TradingView ↗
                    </a>
                  )}
                </div>
                <div className="lab-lib-file">{cur.file}</div>
              </div>

              {!cur.indent_ok && (
                <p className="lab-note">
                  这份是旧版爬虫产物：块缩进被清洗规则吃掉了（if/for 的体没有缩进），
                  TradingView 与任何 Pine 工具都编译不了，只能当参考阅读。
                  修好的爬虫重爬后会自动替换成完整版本（见
                  <code>scripts/pine_library_index.py</code> 的来源优先级）。
                </p>
              )}

              <div className="lab-lib-run">
                <label>
                  <span>回测标的</span>
                  <input
                    className="lab-lib-sym"
                    value={symbol}
                    placeholder="600309.SH"
                    onChange={(e) => setSymbol(e.target.value)}
                  />
                </label>
                <button
                  className="lab-run lab-lib-run-btn"
                  disabled={running || queuing}
                  onClick={run}
                >
                  {running ? '跑着呢…' : queuing ? '入队中…' : '转写并回测'}
                </button>
                {job?.status === 'failed' && (
                  <span className="lab-lib-msg err">{job.error || '失败'}</span>
                )}
                {btErr && <span className="lab-lib-msg err">{btErr}</span>}
              </div>

              {running && (
                <div className="lab-transpile">
                  <span className="tag">{STAGE_LABEL[job?.stage ?? ''] ?? job?.stage}</span>
                  <span className="lab-transpile-stats">
                    宿主上转写 + 沙箱回测，约 30–60 秒
                  </span>
                </div>
              )}

              {job?.status === 'failed' && job.stage === 'static' && (
                <div className="lab-err">
                  静态检查未通过（候选没有进回测）：
                  {(job.problems ?? []).map((p) => (
                    <div key={p}>· {p}</div>
                  ))}
                </div>
              )}

              {report && st ? (
                <>
                  <div className="lab-transpile">
                    <span className="tag">
                      AI 转写 · {tp && tp.problems.length ? `静态未过（${tp.problems.length}）` : '静态通过'}
                    </span>
                    <span className="lab-transpile-stats">
                      回测 {report.symbol}（{report.adj}）· {report.trades} 笔
                      {total ? ` · 胜率 ${num(((wins ?? 0) / total) * 100)}%` : ''}
                    </span>
                  </div>
                  <div className="lab-stats">
                    <div className={`lab-stat ${(st['Net profit']?.pct ?? 0) >= 0 ? 'up' : 'down'}`}>
                      <span className="k">策略收益</span>
                      <span className="v">{fmtPct(st['Net profit']?.pct)}</span>
                    </div>
                    <div className="lab-stat">
                      <span className="k">买入持有</span>
                      <span className="v">{fmtPct(st['Buy & hold return']?.pct)}</span>
                    </div>
                    <div className="lab-stat down">
                      <span className="k">最大回撤</span>
                      <span className="v">
                        {fmtPct(-(st['Max equity drawdown']?.pct ?? 0))}
                      </span>
                    </div>
                    <div className="lab-stat">
                      <span className="k">成交 / 胜率</span>
                      <span className="v">
                        {report.trades}
                        {total ? ` / ${num(((wins ?? 0) / total) * 100)}%` : ''}
                      </span>
                    </div>
                  </div>
                  <Spark points={report.equity ?? []} />
                  {rows.length > 0 && (
                    <div className="lab-trades">
                      <div className="lab-table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>买入</th>
                              <th className="num">价</th>
                              <th>卖出</th>
                              <th className="num">价</th>
                              <th className="num">盈亏</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map((t, i) => (
                              <tr key={`${t.entry_time}-${i}`} className={(t.profit ?? 0) >= 0 ? 'up' : 'down'}>
                                <td>{t.entry_time}</td>
                                <td className="num">{num(t.entry_price, 2)}</td>
                                <td>{t.exit_time ?? '持有中'}</td>
                                <td className="num">{num(t.exit_price, 2)}</td>
                                <td className="num">{fmtPct(t.profit_pct)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                !running && (
                  <p className="lab-note">
                    还没回测过。本机没有 Pine 解释器，.pine 要先翻成 Pyne-Python 才能跑——
                    点上面的「转写并回测」，宿主 worker 会调模型转写、静态检查、再在 bwrap
                    沙箱里回测（断网 + 只读根）。队列由 cron 每分钟取一次。
                  </p>
                )
              )}

              <textarea
                className="lab-lib-editor"
                spellCheck={false}
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setDirty(true);
                  setSaveMsg('');
                }}
              />

              <div className="lab-lib-actions">
                <button className="lab-run lab-lib-save" disabled={!dirty || saving} onClick={save}>
                  {saving ? '保存中…' : dirty ? '保存编辑' : '已保存'}
                </button>
                <button className="lab-more" disabled={!cur.edited} onClick={reset}>
                  丢弃编辑
                </button>
                {saveMsg && <span className="lab-lib-msg">{saveMsg}</span>}
                {saveErr && <span className="lab-lib-msg err">{saveErr}</span>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
