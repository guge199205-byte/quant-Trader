/** Pine 策略库：桌面 TradingView 语料（1001 个）按分类浏览 / 搜索 / 查看源码 / 编辑保存。
 *
 * 语料三种来源，优先级 edited > recrawl > desktop：
 *  - desktop：旧版爬虫产物，块缩进被拍平（TradingView 自己也编译不了），仅可读；
 *  - recrawl：修好爬虫后重爬的，缩进完整；
 *  - edited：本页保存的编辑（写 data/pine_library/edited/，不动桌面原文件）。
 * 索引由 scripts/pine_library_index.py 生成。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchPineList,
  fetchPineSource,
  fetchPineTranspile,
  resetPineSource,
  savePineSource,
  type PineListItem,
  type PineList,
  type PineTranspile,
} from '../../api/client';

const KIND_LABEL: Record<string, string> = {
  desktop: '原始语料',
  recrawl: '重爬（缩进完整）',
  edited: '已编辑',
  missing: '缺失',
};

const PAGE = 200;

const fmtPct = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;

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

  useEffect(() => {
    fetchPineTranspile()
      .then(setTranspile)
      .catch(() => setTranspile({}));
  }, []);

  const open = (id: string) => {
    setSaveMsg('');
    setSaveErr('');
    fetchPineSource(id)
      .then((d) => {
        setCur(d);
        setDraft(d.source ?? '');
        setDirty(false);
      })
      .catch(() => setSaveErr('源码读取失败'));
  };

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
      .catch(() => setSaveErr('保存失败'))
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

  const items = list?.items ?? [];
  const canMore = list ? list.filtered > items.length : false;
  const tp = cur ? transpile[cur.id] : undefined;

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
            <div className="lab-empty">左侧点一个策略 → 这里看源码</div>
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

              {tp ? (
                <div className="lab-transpile">
                  <span className={`tag ${tp.problems.length ? 'warn' : ''}`}>
                    AI 转写 · {tp.problems.length ? `静态未过（${tp.problems.length}）` : '静态通过'}
                  </span>
                  <span className="lab-transpile-stats">
                    {tp.has_report
                      ? `回测 ${tp.symbol}：策略 ${fmtPct(tp.net_pct)} · 买入持有 ${fmtPct(tp.bh_pct)} · 回撤 ${fmtPct(tp.dd_pct)} · ${tp.trades ?? 0} 笔`
                      : '已生成候选，尚未回测'}
                  </span>
                </div>
              ) : (
                <p className="lab-note">
                  AI 转写：还没跑过。本机没有 Pine 解释器，让 .pine 跑起来的唯一路径是翻成
                  Pyne-Python 模板。在宿主上执行
                  <code>scripts/pine_to_pyne.py --id {cur.id} --validate 600309.SH</code>
                  即可（转写要执行模型产出的代码，所以不进 API 进程）。
                </p>
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
