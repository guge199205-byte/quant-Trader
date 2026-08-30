import './Harness.css';

/* ============================================================
   交易智能体 — 嵌入 DeepSeek Harness 工作区（AI-HARNESS）
   dsh 绑 127.0.0.1:3081；iframe 走 ui-arena 的同站点代理端口
   8093（同 host 不同端口 → localStorage 不被第三方存储隔离，
   dsh settings 可用），basic auth 与 3081 同一份凭据。
   iframe 地址用当前 hostname 自适应：localhost/127.0.0.1/局域网 IP
   都能保持"同站点"。首次进入在页面内输入一次 admin 凭据。
   ============================================================ */

const DSH_URL = 'http://192.168.31.68:3081';
const DSH_PROXIED_URL = `http://${window.location.hostname}:8093`;

export default function Harness() {
  return (
    <div className="page">
      <div className="hs-header">
        <div>
          <div className="hs-title">
            交易智能体 <span className="hs-badge">AI-HARNESS</span>
            <span className="hs-sub">DeepSeek Harness 工作区嵌入 · 挂载 MCP 交易工具（行情/搜索/交易/计算/记忆）· 首次进入在页面内输入 admin 凭据</span>
          </div>
        </div>
        <a className="hs-btn" href={DSH_URL} target="_blank" rel="noopener noreferrer">
          新窗口打开 ↗
        </a>
      </div>

      <div className="hs-frame-wrap">
        <iframe
          src={DSH_PROXIED_URL}
          title="交易智能体 (AI-HARNESS)"
          className="hs-frame"
          allow="clipboard-write"
        />
      </div>
    </div>
  );
}
