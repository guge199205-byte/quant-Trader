import './Harness.css';

/* ============================================================
   交易智能体 — 嵌入 DeepSeek Harness 工作区（AI-HARNESS）
   dsh 绑 127.0.0.1:3081，局域网经 dsh-proxy（192.168.31.68:3081，
   basic auth）访问；iframe 内首次需输入一次 admin 凭据。
   ============================================================ */

const DSH_URL = 'http://192.168.31.68:3081';

export default function Harness() {
  return (
    <div className="page">
      <div className="hs-header">
        <div>
          <div className="hs-title">
            交易智能体 <span className="hs-badge">AI-HARNESS</span>
          </div>
          <div className="hs-sub">
            DeepSeek Harness 工作区嵌入 · 挂载 MCP 交易工具（行情/搜索/交易/计算/记忆）· 首次进入在页面内输入 admin 凭据
          </div>
        </div>
        <a className="hs-btn" href={DSH_URL} target="_blank" rel="noopener noreferrer">
          新窗口打开 ↗
        </a>
      </div>

      <div className="hs-frame-wrap">
        <iframe
          src={DSH_URL}
          title="交易智能体 (AI-HARNESS)"
          className="hs-frame"
          allow="clipboard-write"
        />
      </div>
    </div>
  );
}
