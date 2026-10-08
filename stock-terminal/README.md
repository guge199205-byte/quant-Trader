# 老版个股终端 · 本地独立版

从主仓库 `backup-stock-terminal-restore` 分支抽出的**合并前旧布局个股终端**，
独立 Web 应用，仅供本地使用，**不参与主仓库提交**（整个目录在仓库之外）。

## 开机自启（已配置）

- 终端：systemd 用户服务 `stock-terminal`（`~/.config/systemd/user/stock-terminal.service`），
  登录后自动启动，崩溃 5 秒后自动拉起；当前运行的就是它
- 数据后端：quantmind 容器 docker compose 自带 `restart: unless-stopped`，Docker
  服务开机自启即可

维护命令：
```bash
systemctl --user status stock-terminal   # 状态
journalctl --user -u stock-terminal -f   # 日志
systemctl --user restart stock-terminal  # 重启
npm run build                            # 改代码后构建，服务直接服务新产物，无需重启
```

> 限制：用户级服务在**登录后**启动。若要登录前（无头）自启，需
> `sudo loginctl enable-linger zbox`（需要 sudo 权限，按需再开）。

## 启动（手动方式）

```bash
npm install
npm run dev        # 开发模式（改代码热更新；但体感慢——每次交互有模块编译开销）
npm run build      # 生产构建 → dist/
npx vite preview --port 8099 --host   # 生产模式（流畅，日常推荐）
```

打开 **http://192.168.31.68:8099**（局域网同样访问），用本机后端账号密码登录即可。

> 性能结论：**不流畅的根因是 dev 模式逐模块编译 + StrictMode 双请求，不是查询慢**。
> 实测每个接口经「页面→代理→8000 网关→存储」只要 9-24ms；生产构建后页面
> 加载 240ms、终端 0.5s 就绪。改了代码要 `npm run build` + 重启 preview。
> 不要为了"流畅"去接 DuckDB——本机数据在 `data/quantdb`（GB 级 parquet），
> 浏览器端直读不现实，服务端直读也不会快过现在的 20ms；等真有接口
> 超数百 ms 再为单个接口做 DuckDB 快路径（参考 engine 的 quantdb_hub）。

## 架构

- 前端文件：`src/app/**`（原样保留 `electron/src` 层级的相对导入，
  来自备份分支，未做任何业务改动）
- 样式：沿用原版整套 —— `tailwindcss@3.4.4` + postcss + 原版
  `tailwind.config.cjs`、`src/styles/global.css`（含全部设计变量）、
  `research-next-theme.css`、`mac-theme.css`；**不要删 global.css**，
  终端的布局/配色全靠它（配色是 hsl(var(--xxx)) 变量方案）
- 数据源：全部请求走**同源代理**（Vite → `127.0.0.1:8000`，含 WebSocket），
  不需要给后端配 CORS；后端为本地 docker 的 quantmind 容器（8000-8003）
- 端口：8099（`vite.config.ts` 里改；`.env` 里的 VITE_API_BASE_URL 需同步改）
- 登录态：`localStorage`（本 origin 独立，与 8080 主应用互不影响）

> 坑位记录：package.json 是 `"type": "module"`，postcss 配置必须叫
> `postcss.config.cjs`（.js 会被当 ESM 解析报 module is not defined）。

## 文件构成（35 个源文件，全部来自备份分支）

```
src/app/
├── config/services.ts                  # API 基址配置（env 驱动）
├── services/{research,realTrading,modelTraining}Service.ts
├── features/stock-terminal/**          # 终端本体（23 个文件，旧布局）
├── features/auth/services/authService.ts
├── features/auth/types/
├── features/research/types.ts
├── features/admin/types.ts
├── types/liveTrading.ts
└── utils/{errorHandler,performance}.ts
```

## 注意

- Vite dev 模式不强制 TS 类型检查；旧代码按原样运行
- 若后端地址不是 `127.0.0.1:8000`，改 `vite.config.ts` 的 proxy target + `.env`
- 这是老布局快照；若想要更新版（当前 master 上 V2 终端），不在此应用内