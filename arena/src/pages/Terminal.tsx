import './Harness.css';

/* ============================================================
   个股终端 — 嵌入独立子应用（8094）
   与 /harness 同一套路：独立端口承载独立应用，页面只负责 iframe。

   为什么用独立端口而不是子路径：
     终端的 /api 反代到 quantmind 网关 8000，而 arena 自己的 /api 反代到
     baymax-api 8091 —— 同路径不同语义，放同一 origin 会打架。
     独立端口让两个 /api 各归各。

   容器：baymax-terminal（见 docker-compose.yml 的 terminal 服务），
         host 网络 + nginx 反代，源码在 ./stock-terminal。

   iframe 地址按当前 hostname 自适应（localhost / 127.0.0.1 / 局域网 IP），
   与 dsh 那页同理，保证"同站点"。
   ============================================================ */

const TERMINAL_PROXIED_URL = `http://${window.location.hostname}:8094`;

export default function Terminal() {
  return (
    <div className="page hs-page">
      <div className="hs-frame-wrap">
        <iframe
          src={TERMINAL_PROXIED_URL}
          title="个股终端 (Stock Terminal)"
          className="hs-frame"
          allow="clipboard-write"
        />
      </div>
    </div>
  );
}
