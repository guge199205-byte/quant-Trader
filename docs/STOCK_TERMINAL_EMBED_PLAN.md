# 个股终端内嵌进 Arena（实施方案）

> 目标：把本机 `/home/zbox/stock-terminal-local/` 的个股终端，作为 Arena 的一个入口
> **放在「实况」之后**，网页能打开即可用。
> 成功判据（用户口径）：**网页能打开、功能能起来**。

---

## 0 · 代码安全（用户明确要求：代码不要丢失）

| 规则 | 做法 |
|---|---|
| **绝不动原件** | `/home/zbox/stock-terminal-local/` 全程只读，不删不改不移动 |
| **只做复制** | 拷进仓库时用 `cp -a`（保留权限/时间戳），不用 `mv` |
| **可验证** | 复制后逐文件比对 sha256，并在计划里留基线清单 |
| **不进 node_modules / dist** | 只拷源码与配置（59 个文件），产物在镜像里重建 |

---

## 1 · 为什么不合并源码

| 维度 | arena | 个股终端 |
|---|---|---|
| react-router | **6** | **7** |
| UI 库 | visx / lightweight-charts | **antd 5 + echarts 6** |
| 样式 | 自有 CSS | **Tailwind**（preflight 会重置 arena 全局样式） |

硬合并会撞三处：路由大版本不兼容、Tailwind preflight 污染 arena 样式、打包体积翻倍。
终端还自带 `LoginGate` 和自己的 API 路由。

**结论：走"独立子应用 + 独立端口 + iframe"**——这正是 Arena 已有的模式
（`/harness` 就是 iframe 嵌 8093 的 dsh）。

---

## 2 · 架构

`baymax-ui-arena` 一个 nginx 容器已经服务两个端口，加第三个：

```
8092  Arena SPA（现有，/api → baymax-api:8091）
8093  dsh 智能体（现有，basic auth → dsh-proxy:3081）
8094  个股终端（新增）→ 静态托管终端 dist + /api /health /ws → quantmind 网关 127.0.0.1:8000
```

**为什么是独立端口而不是子路径**：终端的 `/api` 与 Arena 的 `/api` 语义不同
（前者→quantmind 网关 8000，后者→baymax-api 8091）。独立端口各归各，不打架。

Arena 侧新增 `/terminal` 路由页（iframe `http://<hostname>:8094`）+ 导航项插在「实况」之后。

---

## 3 · 三个必须处理的坑（已实测确认）

### 坑 1 · API 基址是构建期烧死的

`.env` 里 `VITE_API_BASE_URL=http://192.168.31.68:8099`（**它自己的 dev 端口**），
且已构建的 `dist/` 里同时存在 `http://127.0.0.1:8000` 和 `http://192.168.31.68:8099`。
直接部署会连到 8099 → 打不开。

`getBaseUrl()` 的优先级链（源码 services.ts:204）：

```
dynamicServerUrl(运行时用户配置) → persisted(localStorage) → VITE_API_BASE_URL → [Electron] 127.0.0.1:8000 → 返回 API_BASE
```

**修法：打包时把 `VITE_API_BASE_URL` 留空** ⇒ `API_BASE=''` ⇒ 非 Electron 环境返回 `''`
⇒ **相对路径同源请求**，由 8094 的 nginx 代理到网关。
（同时要清掉 `localStorage['quantmind_server_url_v2']` 的干扰——新 origin 天然没有。）

### 坑 2 · WebSocket 路径带 `/ws` 前缀

`getWebSocketUrl()` 的 Web 部署分支用的是
`${protocol}//${window.location.host}/ws/api/v1/ws/market`
——**与 HTTP 的 `/api/v1/...` 不同前缀**。所以 nginx 必须同时代理 `/ws`。

### 坑 3 · 自带 LoginGate

`LoginGate` 检查 `localStorage.access_token`，登录走 `authService.login()` 打后端 8000；
后端确实要鉴权（实测 `/api/v1/simulation/account` → **401**）。

**默认保留登录门**（用你自己的 quantmind 账号登）。
如需"打开就能看"，构建期设 `VITE_DISABLE_AUTH=true`（源码 authService.ts:40 支持）——
但数据接口仍会 401，页面能开、数据未必有。**先按保留处理，按实际表现再调。**

---

## 4 · 实施步骤

| # | 动作 | 风险 |
|---|---|---|
| 1 | `cp -a` 终端源码到仓库 `stock-terminal/`（排除 node_modules/dist），sha256 校验 | 无（只读复制） |
| 2 | 加 `.env.production`：`VITE_API_BASE_URL=`（空）+ 按需 `VITE_DISABLE_AUTH` | 无 |
| 3 | `arena/Dockerfile` 加第二个构建阶段：build 终端 → 产物放 `/usr/share/nginx/html/terminal` | 中（改镜像） |
| 4 | `arena/nginx.conf.tpl` 加 `listen 8094` server 块（静态 + `/api` `/health` `/ws` 代理） | 中 |
| 5 | `docker-compose.yml` 给 ui-arena 发布 8094 | 低 |
| 6 | Arena 加 `pages/Terminal.tsx`（照抄 Harness.tsx 的 iframe 模式）+ `App.tsx` 路由 + `Navbar.tsx` 插在「实况」后 | 低 |
| 7 | 打回滚镜像 → 重建 ui-arena → 验证 | 有回滚 |

**回滚**：`docker tag baymax-ui-arena:latest baymax-ui-arena:pre-terminal-rollback` 先建，
出事就 `docker compose up -d ui-arena` 换回旧镜像。

---

## 5 · 验收

1. `http://192.168.31.68:8094/` 返回 200 且是终端页面（不是 404/白屏）
2. Arena 导航「实况」后面出现新入口，点进去 iframe 正常渲染
3. 终端页面能拉到数据（或至少明确报出后端错误，而不是静默白屏）
4. `docker compose up -d ui-arena` 重建后 8092/8093 两个现有入口**不受影响**

---

## 6 · 待定项（按实际表现再定）

- **登录门**：当前**保留**（`.env.production` 里 `VITE_DISABLE_AUTH=false`）。
  登录走终端自己的 `authService.login()` 打网关 8000，用你的 quantmind 账号。
  若要"打开即看"，改成 `true` 重建即可——但**数据接口仍会 401**，只是跳过登录界面。
- **导航项命名**：暂用「个股终端」，要改说一声。
- **终端源码是否提交进 git**：原目录标注"不参与主仓库提交"。
  镜像构建需要它在磁盘上，**提不提交是独立决定**，不影响功能。

## 7 · 实际做法（与计划的偏离，2026-09-13 已实施并验收）

**偏离一处**：计划里是"在 arena 的 nginx 加第三个 server 块（8094）"，
**实际改成了独立容器 `baymax-terminal`**（自带 nginx，`network_mode: host`，监听 8094）。

原因：改 arena 镜像就要重建正在服务 8092/8093 的容器；独立容器**完全不碰它**，
风险为零，也符合本仓库"一容器一职责"的既有风格（dsh / dsh-proxy / ui-arena 各自独立）。

**落地清单**：

| 件 | 位置 |
|---|---|
| 终端源码（复制，原件未动） | `stock-terminal/` |
| 生产构建覆盖（基址清空） | `stock-terminal/.env.production` |
| 镜像 | `stock-terminal/Dockerfile`（多阶段：node:20-alpine → nginx:alpine） |
| 反代规则 | `stock-terminal/nginx.conf`（`/api` `/health` 原样、`/ws/` **剥前缀**） |
| 服务 | `docker-compose.yml` 的 `terminal`（`baymax-terminal`，host 网络，8094） |
| Arena 页面 | `arena/src/pages/Terminal.tsx`（iframe，复用 Harness.css 布局） |
| Arena 路由 | `arena/src/App.tsx` → `/terminal` |
| Arena 导航 | `arena/src/components/Navbar.tsx` → 「个股终端」插在「实况」之后 |

**验收结果（全部实测）**：

| 检查 | 结果 |
|---|---|
| `http://192.168.31.68:8094/` | **200**，`<title>老版个股终端 · 本地独立版</title>` |
| 反代透明性 | `/health` 200、`/api/v1` 404、`/api/v1/simulation/account` 401 —— **与直连网关 8000 逐条一致** |
| WebSocket | `/ws/api/v1/ws/market` → **101**，与直连网关同结果（前缀剥离正确） |
| 产物无 dev 残留 | `192.168.31.68:8099` **已消失**（`.env.production` 覆盖生效） |
| 导航顺序 | 实况(167386) 在 个股终端(167539) **之前** |
| 现有入口未受影响 | 8092 / 8092/harness / 8093 **全部 200** |
| 代码安全 | 原件 `stock-terminal-local/` 56 文件 sha256 逐一比对**完全一致**，一个字节未动 |

**回滚**：`baymax-ui-arena:pre-terminal-rollback`；终端容器独立，`docker compose rm -sf terminal` 即可摘除。

**注意**：`docker compose up -d ui-arena` 会连带重启依赖的 `baymax-api`（`depends_on`），
重建前留意这个连带影响。
