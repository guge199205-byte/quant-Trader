# Agent 工作流可观测与治理（生产方案）

> 定位：把散落在 **crontab(何时) + Python 脚本(干什么) + cordis patch(工具/persona/模型) +
> 调用点任务文本(具体指令)** 四处的 agent 工作流，收敛成**可看、可判、可改**的一层。
> 原则：**证据优先于计数**、**只读优先于自愈**、**升级/改造先行为对齐再谈能力**。

---

## 0 · 问题定义（有前科，不是设想）

系统现有 **50 个 cron 任务**、17 个常驻容器。已有监控覆盖前两层，第三层是空的：

| 层 | 判据 | 现状 |
|---|---|---|
| 进程活着 | TCP 端口通 | ✅ `status-probe.sh`（每分钟）→ `logs/service_status.json` |
| 任务跑了 | cron 触发 / 容器在跑 | 🟡 `auto-heal.sh` + journalctl；只看容器，不看 50 个任务 |
| **任务干出了活** | **产出物存在且内容有效** | ❌ 无 |

**两层实证（本文档的证据基线）**：

1. **2026-09-10 假绿**（`daily_report_agent.py` 内注释实录）：7 轮全是「本轮分析未执行」
   （桥假活），日报却写"8 轮满额运行、流程纪律正常"——**只数条数不看内容，等于替哑火的链路盖章**。
   独立佐证：当日 `~/.dsh/sessions` 只落 **6 个会话**（前后交易日为 21/16 个）。
2. **poller 型任务不可判定**：`ops_worker.log` mtime 停在 09-04、`pine_chat_worker.log` 停在 09-09。
   这两个每分钟任务究竟是"队列空、worker 健康"还是"已静默死亡十天"，**从日志无法区分**。

---

## 1 · 数据基础（已实测，非假设）

**关键事实：盘中/研究 agent 本身就是 dsh headless 会话**——`scripts/dsh_agent.py` 以
`dsh --profile headless --patch <...> "<任务>"` 调用，`cwd=$HOME`。因此每次运行已原生落盘完整轨迹：

| 项 | 值（2026-09-13 实测） |
|---|---|
| 落盘位置 | `$DSH_HOME/sessions/--home-zbox--/session-*/` |
| 规模 | **376 个会话 / 46 MB** |
| 轨迹完整度 | `tool/call` 1564、`tool/result` 1564、`step/start` 1116、`assistant/message` 1111、`llm/retry` 4 |
| 会话文件格式 | **v2 = `session.jsonl.zstd`（≤0.1.1-rc.2）**；**v3 = `session.v3.jsonl.zstd`（0.1.5-rc.1+）** |
| v3 差异 | 新增 `system/message`；**流式 `text-chunks`/`reasoning-chunks` 不再落盘** |

**会话计数异常成立**：09-10 只落 **6 个会话**（09-09 为 21、09-11 为 234），
与「7 轮全是本轮分析未执行」的假活记录吻合。

### ⚠️ 判据修正：单侧计数指标会产生假阳性（实测踩坑记录）

**初版判据（已废弃）**：按"这一轮调了几次工具"判是否干活。实测 376 个会话后得到
"09-11 有 33 个 glm 交易会话零工具调用"，一度被当成疑似大面积假活。

**人工核对内容后推翻**：那 33 个会话**全部产出了完整交易分析正文**
（如「①【总体总结】震荡弱市下持有万华化学…」「断路恢复校验：窗口 75.81 → 恢复快照 75.81，零跳变」），
`turn/end = completed`，单轮 20181 输入 / 1585 输出 token。
**它们不调工具是正确的**——persona 明确：盘面/L2/五档/新闻/候选池**均由调用方注入**，
agent 无需自己取数。

**教训（两条，都进代码规范）**：
1. **"零工具调用"在本工作负载下不是"没干活"的信号**。调用方预注入数据是常态，
   全库 376 个会话里有 **62 个零工具会话产出了正常正文**。
2. 单侧计数指标上线前，**必须抽随机样本人工核对内容**。本次若不复核，
   会把 33 个正常轮次误报为故障——与 09-10 那次「只数条数不看内容」是**同一个错误的反方向**。

**修正后的判据（本方案采用）**：

| 信号 | 来源 | 用法 |
|---|---|---|
| `turn/end.reason.kind` | 会话 | `completed` / `error`（**最硬的一手信号**） |
| assistant 正文非空 | 会话 | 空正文 = 真没产出 |
| 输出 token 数 | 会话 `assistant/chunk.usage` | 产出量的量级校验 |
| 业务产出物 | 池文件/成交/报告/净值采样 | **最终判据**，见阶段 3 |
| 自报 vs 实测工具清单 | persona 自报 + 会话 `tool/call` | **仅在该 job 预期需要工具时**使用 |

**376 会话实测基线**：`completed` 371 / `error` 4 / 无收尾 1；空正文 5。
4 个 error 全部落在 **09-01 建置期**（3× GLM `max_tokens` 超限——后由
`baymax.glm.cordis.yml` 的 `maxTokens: 8192` 修复；1× 缺 API key），**均为历史问题，非现行故障**。

---

## 2 · 四阶段实施

编号对应"步骤 1-4"。**每阶段独立可交付、可回滚、可单独验收**。

### 阶段 1 · 运行归因（job 标识）

**目标**：让每一次 agent 运行可归因到具体任务。

- **方案**：`dsh_agent.py` 的 `run_agent()` 增加可选 `job` 参数，在任务文本前置短标识
  `【job:<id>】`。理由：一行改动、不依赖 dsh 内部 API、v2/v3 会话均可解析。
  备选（session title、patch 注入 system message）均依赖模型生成或私有 API，**不采用**。
- **产出**：`scripts/dsh_agent.py`（签名向后兼容，不传 `job` 时行为完全不变）；
  各调用点（`live_hourly_analysis` / `night_pool_agent` / `post_review` / `risk_budget_agent` /
  `event_radar` / `debate_arbiter` 等 10 处）传入自己的 job id。
- **⚠️ 生产约束：这是改 prompt，必须先用输出回归证明无害。**
  任务文本进入 user message，可能影响模型输出。**动线上前必须做 A/B**：同一批历史任务，
  加 / 不加前缀各跑一遍，验证决策 JSON 的 `parse_intraday_decision` 可解析率不下降。
  验证未过 → 阶段 1 不上线，改用 patch 注入标识（代价更高但不动任务文本）。
- **验收**：新增会话 100% 可归因；历史会话标 `unknown` 且不报错；A/B 可解析率无回归。
- **测试**：单测覆盖"注入前缀 / 解析回来 / 不传时零变化"三分支。

### 阶段 2 · 证据读取器（看）

**目标**：把会话轨迹变成"每个 job 今天跑了几轮、每轮调了什么工具、有没有干活"的事实层。

- **输入**：`$DSH_HOME/sessions/*/session-*/*.jsonl.zstd`（**同时支持 v2 与 v3**）。
- **产出**：`logs/work_ledger/{date}.jsonl`，每会话一行，契约字段：
  `session_id / job / cwd / created_at / steps / tool_calls / tools_used{} / retries /
  has_output / format(v2|v3)`（契约带 `schema_version`，后续变更不破坏消费者）。
- **实现要点**：
  - **只读**：绝不写 session 目录；解析失败按"单会话降级"处理，不中断整批。
  - **增量**：按游标 + mtime 只解析新增会话，避免每次全量解压 376 个文件。
  - **容错**：`zstd` 截断/半写文件（会话正在写）跳过并记账。
- **验收**：用现有 376 个会话回放，逐日计数与本文档基线一致；**能重现 09-10 的 6 会话异常**；
  v2/v3 各自 fixture 通过。
- **测试**：v2 fixture + v3 fixture + 损坏文件 + 空会话。

### 阶段 3 · 期望表与判据（改）

**目标**：声明"每个 job 该在什么时候、至少产出什么"，并对事实层出三态判定。

- **产出 A**：`configs/agent_jobs.yaml` —— 声明式 job manifest。
  每 job 字段：`id / schedule(与 cron 对齐) / trading_day_gate / task_source / model / persona_patch /
  expect{evidence, min, grace}`。
- **产出 B**：判据器，三态：
  | 态 | 含义 | 对应现实 |
  |---|---|---|
  | `ok` | 该跑且干出了活 | — |
  | `missed` | 该跑没跑 | 调度断链 / 进程死 |
  | `hollow` | **跑了没产出** | **09-10 假活、桥假活** |
- **判据必须是产出物，不是日志行数**（09-10 教训）。三类可利用证据：
  1. 会话轨迹（阶段 2）：工具调用数、步骤数、是否有 assistant 输出
  2. 落盘产出物：`logs/night_pool/{date}.json`、`logs/daily_report/{date}.md`、
     `decision_pool.jsonl`、成交流水、净值采样条数……（**条数 + 非空 + 内容有效性**）
  3. **自报对账**：persona 要求模型自报 `已调用：<工具>×N` → 与阶段 2 实测比对
- **⚠️ 与 crontab 的关系——分两步，先对账后生成**：
  - **第一步（本次做）**：manifest **只声明期望**，crontab 完全不动；两者不一致 → 告警。
  - **第二步（验证后再议）**：由 manifest 生成 crontab。crontab 是实盘调度入口，
    **不在一开始替换**。
- **现成载体（待评估）**：`chicheng-cron` 是已装的 **dsh 原生调度插件**
  （支持 cron 定时执行 shell / python / nodejs 脚本、skills、agent 任务，含推送与归档）。
  第二阶段的"由 manifest 生成调度"可能不必自己写——但**它当前在 0.1.5 下未验证可用**
  （见 §4.3），选型前需先确认。
- **灰度**：判据器先**只出报表不告警**，人工核对 2-3 个交易日无假阳性后，再接 `alert.sh`。
- **验收**：回放 09-03~09-12 历史，能正确标记 09-10 的 `hollow`/`missed` 且无大面积误报。
- **测试**：`test_config_integrity.py` 同款模式校验 manifest（字段齐全、job id 与 cron 一一对应）。

### 阶段 4 · 面板

**目标**：把事实层 + 判据渲染成可看的视图。**两种形态，按代价递增，先摘低垂果实**。

| 形态 | 做法 | 代价 | 依赖 |
|---|---|---|---|
| **(a) 现有 API + 页面** | 在 `baymax-api` 加只读端点 + 页面（复用 `/api/metrics` 模式） | 小 | 无 |
| **(b) dsh web 插件** | 写 dsh 面板插件，嵌进 3081 | 大 | **容器 harness 必须升级** |

**建议**：先做 (a) 拿到全部价值；(b) 作为独立阶段，在容器 harness 升级完成后单独评估。

- **只读边界（硬约束）**：面板只读日志/状态/台账，**不碰交易执行路径**，
  尤其**不做"自动重启 agent"**——自愈已由 `auto-heal.sh` 负责，面板职责是**发现假活**，
  两者混在一起会重新变成互相盖章。

---

## 3 · 横切生产要求

| 项 | 要求 |
|---|---|
| **只读边界** | 全程不写交易执行路径；监控自身故障**不得影响交易**（观测失败 → 降级为不可见 + 自身告警） |
| **失败姿态** | 明确 **fail-open**：监控挂了不影响 agent 运行；但监控自身的"没数据"要能与"没干活"区分 |
| **数据保留** | ledger 按日分片，对齐 `log_cleanup.py --days 30` 的保留策略 |
| **配置完整性** | manifest 加校验测试（复用 `tests/test_config_integrity.py` 模式） |
| **灰度** | 每阶段先"只读/只报"，人工核对后再接告警；单阶段独立回滚 |
| **可观测性** | 判据器自身的运行也要留痕（谁在何时判了什么），否则又是一个黑盒 |

---

## 4 · 已完成：harness 升级（2026-09-13）

**这是阶段 4(b) 的前置，也是本方案数据层的前提**（v3 会话格式）。

| 项 | 内容 |
|---|---|
| 目标 | 宿主 dsh **0.1.1-rc.2 → 0.1.5-rc.1** |
| 方式 | **并行安装**到独立前缀 `/home/zbox/.local-dsh-0.1.5`，验证后切换软链 `~/.local/bin/dsh` |
| 旧版 | **原地保留未删**，可秒回滚 |
| 回滚命令 | `ln -sfn ../lib/node_modules/@deepseek-ai/dsh/lib/bin.js ~/.local/bin/dsh` |

**兼容性验证（切换前完成）**：同一套补丁在旧/新版下 `--dump-config` 逐行 diff。
- ✅ 9 个交易关键条目（`system-prompt` persona + 6 个 MCP + `agent-default-model` + `llm-deepseek`）**原样保留**
- ✅ 只读冒烟：新版连 MCP 取到真实行情；**真实交易补丁 + 禁用 `mcp-trade`** 全路径验证通过
- ✅ 切换后走**真实生产代码路径**（`dsh_agent.py`）验证通过
- ✅ 两个被移除的内置工具（`tool-str-replace-editor`、`tool-subagent-report`）**全仓库无引用**

**新版默认值变更 → 行为对齐补丁**（`~/.dsh/profiles/headless/cordis.patch.yml`）：

| 变更 | 旧默认 | 新默认 | 处置 |
|---|---|---|---|
| `session-telemetry-otel.mode` | `DISABLED` | `FEEDBACK_ONLY` | **钉回 DISABLED**（保留 env 覆盖口子） |
| `tool-web.fetch` | `false` | `true` | **钉回 false**（新增网页抓取能力是独立决定） |

> 原则：**升级保持行为不变**；启用新能力（网页抓取、遥测）应是显式决定，不是升级的副作用。
> 要启用时删掉补丁里对应条目即可。

### 4.1 交易路径不受影响（关键安全确认）

| 事实 | 值 |
|---|---|
| 交易 agent 用的 profile | `headless`，bundles = `[@deepseek-ai/dsh-base, @deepseek-ai/dsh-headless]` |
| 第三方插件 | **一个都不加载** → 与下述插件不兼容问题**完全隔离** |
| 切换后实测 | 走真实 `dsh_agent.py` 路径取数成功；`--status` 五个 MCP 全通 |

### 4.2 方法学教训：`--dump-config` 通过 ≠ 能跑

**实测反例**：`--dump-config` 对宿主 web profile 返回 exit 0、4 个插件全部出现在组合结果里；
但**真实启动同一 profile 时插件树加载失败**。配置能组合不代表运行时 API 兼容。
→ 凡涉及第三方插件的验证，**必须做一次真实 boot**，不能只 dump。

### 4.3 宿主 web profile 当前不可启动（4 个插件 2 个不兼容）

启动实测（`dsh --profile web`）产物：

| 插件 | 错误 | 性质 |
|---|---|---|
| `dsh-plugin-finance-data` | `unsupported JSON schema: parameters.targets.additionalProperties must be explicitly true or false` | 新版收紧了 tool JSON schema 校验 |
| `dshmarket@1.35.0` | `@deepseek-ai/dsh-settings does not provide an export named 'installSettingsSection'` | **API 破坏性变更**；其 peer 上限为 `0.1.2-alpha.2`，即便升到 `1.45.1` 仍不含 0.1.5 |
| `chicheng-cron` | `store flush failed: ENOENT .../.dsh/cron/store.json.tmp` | 非致命（首次 flush 早于 mkdir 的插件自身 bug），现已自行建目录 |

**因果链（诚实记录）**：`dsh plugin add`（今晨按需求执行）把 4 个插件写进了 `dsh.profile.bundles`；
在此之前它们只在 dependencies 里、**不会加载**，web profile 也不会因它们启动失败。

**处置：已由"守护启动器"解决（不再需要在 A/B 之间二选一）**，见 §4.6。

### 4.4 附带发现：`scripts/start_dsh.sh` 是坏的（**先于本次升级**）

仓库 `.env` 含 `DSH_BIND_IP`，而 dsh 拒绝任何由 `.env` 文件设置该键的启动
（"only the launching environment may set"）。`start_dsh.sh` 会 `cd` 到仓库根目录，必然命中。

- **归因实验**：旧版 `0.1.1-rc.2` 从仓库根目录启动 → **报同一错误**；且 `set -a; source .env` 显式导出后**仍然报错**（dsh 只认"不在文件里"）。
- **结论**：**与本次升级无关**，是既有缺陷。这很可能正是宿主 web 一直没跑起来、全部走容器的原因。
- **修法**：启动前把 `.env` 里的 `DSH_BIND_IP` 移出文件（改为由启动脚本 export），或从非仓库目录启动。

### 4.5 守护启动器（借鉴 dsh_desktop「守护瀑布」，已实现并实测）

**来源**：`dsh_desktop`（Tauri 桌面客户端）的"守护瀑布"——内核 boot 链逐级自愈，
坏插件自动修复、坏配置自动重建、崩溃环原地重启，任何不兼容形态都不退出。
它反证了一件事：**坏插件导致 boot 失败在这个生态里足够常见**，常见到值得专门做防御。
（查证：dsh 0.1.5 加载器**没有**"失败不阻塞"的开关键，CLI 也只有 `--patch` 等七个旗标，
所以这层自愈只能在**启动器/supervisor 层**自己实现。）

**实现**：`scripts/dsh_supervised.sh`。做四件事：
1. 启动 dsh；失败后从**日志失败签名**识别导致插件树加载失败的条目
2. 把这些条目写进隔离补丁（`disabled: true`）后重试——**降级可用优于整树不起**
3. 有界重试（默认 3 次），到上限带诊断退出交给告警，**不无限重启**
4. 非插件原因失败（日志无签名）原样返回，不猜测

**两个实测踩出来的设计要点**（都不是想出来的）：
- **不能用"退出耗时"判启动失败**：dsh **先绑端口、后加载插件树**，失败可能发生在启动 30s 后，
  用时间窗会把启动失败误判成"运行后退出"而放弃隔离。
- **必须排除聚合条目 `include (cordis:include)`**：它是 include 机制本身而非坏插件，
  误禁会把整棵树一起关掉。
- 占位空数组 `[]` 后面直接追加列表项是**非法 YAML**，追加前须先清空。

**实测结果**（对着真实的坏 profile）：

```
第 1/3 次启动 → 检测到插件树加载失败，失败条目: dsh-market finance-data
            → 已隔离 dsh-market / finance-data
第 2/3 次启动 → 服务起来（HTTP 401 鉴权正常）
```

即：**只有坏插件被降级，同 profile 里其它插件（chicheng-cron / chicheng-push）照常工作**——
比"整个 profile 起不来"或"一刀切收回全部插件"都更精确。

### 4.6 容器 `baymax-dsh` 升级完成（2026-09-13）

| 步骤 | 结果 |
|---|---|
| 补丁真实 boot 验证（升级前） | ✅ 宿主上以 0.1.5-rc.1 + `baymax.cordis.yml` 起 web，正常监听、无致命错误 |
| 镜像重建（`ARG DSH_VERSION=0.1.5-rc.1`） | ✅ 镜像内实测 `dsh --version` = 0.1.5-rc.1 |
| 容器重建 | ✅ 三个入口（127.0.0.1:3081 / 192.168.31.68:3081 / :8093）全部响应 401 |
| 启动日志 | ✅ 无 error / 无插件加载失败 |
| 容器内 MCP 连通性 | ✅ 8100–8105 六个端点全通（用 node 测；容器 `sh` 是 dash，不支持 `/dev/tcp`，用它会得到假阴性） |
| **会话历史** | ✅ 升级前后均为 5 条，未丢 |
| **回滚镜像** | ✅ `baymax-dsh:pre-0.1.5-rollback`（= 原 0.1.1-rc.2 镜像 2eea59a6238c） |

**未对容器套用守护启动器**，理由：容器 profile 无第三方插件（plugin 失败类风险不适用），
而把 bash 包成 PID 1 会引入信号转发问题（`docker stop` 可能只能靠 SIGKILL 收尾），
**收益低而风险实在**。容器的崩溃环由既有 `alert.sh`（每 5 分钟探 3081）+ `auto-heal.sh` 兜底。

### 4.7 升级引入的行为变更：web 端新增**强制 token 鉴权**（影响日常访问）

**现象**：升级后浏览器打开 dsh web 显示 `dsh web authentication required; reopen the URL printed by dsh web.`

**归因实验（对照旧版镜像）**：

| 版本 | 启动行 |
|---|---|
| 0.1.1-rc.2（旧） | `dsh web: http://127.0.0.1:3094` ← **无 token** |
| 0.1.5-rc.1（新） | `dsh web: http://127.0.0.1:3081/?token=89dd…` ← **带 token** |

→ **0.1.5 起 web 端强制 token 交换**，旧版没有这层。这是升级的副作用，不是配置错。

**机制（读源码 + 实测确认）**：
- 首次访问必须打开带 `?token=` 的 URL 完成交换 → 服务端下发签名 cookie（`Max-Age` 30 天）
- **cookie 按 authority(host:port) 绑定**（cookie 载荷里写着 `"authority":"…"`，cookie 名是该 authority 的 sha256）
  → **必须在你实际访问的那个地址上做交换**，从 `127.0.0.1` 换来的 cookie 不能用在 `192.168.31.68` 上
- **签名密钥持久化在 `$DSH_HOME/.credentials.yaml`** → **cookie 跨容器重启有效**
  → 正常情况下这是**一次性操作**，不必每次重启都重新交换
- URL 里的 token 是**进程级**的，随重启更换 → 仅用于"cookie 丢了"时重新交换

**实测链路**（经代理 + Basic auth）：

```
GET http://192.168.31.68:3081/?token=…  → 303 See Other + set-cookie: dsh-auth-…（30 天）
带该 cookie 访问 /                       → HTTP 200
```

**取当前入口**：`bash scripts/dsh_web_url.sh`（从容器日志提取并拼成 LAN 地址）。

**排查陷阱**：`http://192.168.31.68:3081/` 返回的 401 有两层来源，别混：
- **nginx Basic auth**（`Server: nginx/1.31.4` + `WWW-Authenticate: Basic realm="dsh - Quant Agent Trader"`，
  由 `baymax-dsh-proxy` 提供，默认 `admin/admin123`）——**这一层是原有的**
- **dsh 的 token 鉴权**（`authentication required; reopen the URL…`）——**这一层是升级新增的**

### 4.8 鉴权问题的修复方案（已实施）

**目标**：把 §4.7 新增的那层 token 鉴权在代理层透明补上，恢复到升级前的使用体验，
同时**不削弱外层 basic auth**。

**三个组成部分**：

| 件 | 位置 | 作用 |
|---|---|---|
| `cookieMaxAgeDays: 3650` | `dsh/baymax.cordis.yml` 的 `connection` 插件 | 把 cookie 上限从 30 天放宽，签一次长期有效 |
| `scripts/dsh_auth_cookie.py` | 仓库 | 用 dsh **持久化**签名密钥预签 cookie，写 `dsh/proxy/dsh-cookies.conf` |
| `proxy_set_header Cookie $dsh_auth_cookie` | `dsh/proxy/nginx.conf.tpl` | 代理转发时注入该 cookie |

**原理**：cookie 的签名密钥存在 `$DSH_HOME/.credentials.yaml`（容器重启不变），
所以可以离线预签。签名格式严格对齐 `BrowserAuth`：
`v1.{b64url(payload)}.{b64url(hmac_sha256(secret_bytes, body))}`，
cookie 名 = `dsh-auth-` + `b64url(sha256(authority))`。
校验通过的实证：带正确 cookie → **200**；不带 → 401；用错 authority 的 cookie → 401。

**⚠️ 本次踩到的最大坑：补丁条目的 `config` 是整体替换、不是合并**

第一版只写了 `cookieMaxAgeDays`，结果把 `dsh-web-app` 原有的一并抹掉了：

```yaml
# dsh-web-app/cordis.patch.yml 原文
- id: connection
  config:
    trustedHosts: !!js ctx.webRuntime.trustedHosts   # ← 被我的补丁顶掉
```

`trustedHosts` 变 undefined 后，`/api` 的浏览器信任围栏拒绝所有 LAN authority：
**首页能开（200）、但所有 `/api/*` 与实时流返回 403**（`forbidden`，9 字节）。
对照实验定位：空补丁 → 围栏 401（通过）；加该补丁 → 403（拒绝）。
修法：把原表达式一并重写。

**这个坑对仓库里其它补丁同样成立**，已做审计：

- `baymax.trading.cordis.yml` 的 `system-prompt` 把基线的 `personaPrefix`/`personaSuffix`
  **整体顶掉了**（对交易 agent 语义上大概率正合意，但属"顺带消失"而非显式决定）
- `agent-default-model` 的 `model` 被覆盖，属预期
- **风险在于未来**：dsh 新版本往这些插件的 config 里加字段时，会被本仓库的补丁**静默丢弃**

**安全边界（明示取舍）**：

- 外层 nginx basic auth **保持不变**，仍是第一道门；本改动只是把升级新增的第二道门
  由代理代为通过，恢复到 0.1.1-rc.2 的实际效果
- 代价：cookie 是长期凭据，落在 `dsh/proxy/dsh-cookies.conf`（**已加入 .gitignore**，勿入库）
- 外层凭据若泄露，网关的 30 天→3650 天延长会放大影响面——轮换 basic 口令即可缓解
- 重新签发（改口令/换 authority/改 cookieMaxAgeDays 后）：
  `python3 scripts/dsh_auth_cookie.py && docker restart baymax-dsh-proxy`
- 缺失 `dsh-cookies.conf` 时 entrypoint 会写**透传兜底** map（回到手动交换），不会起不来

### 4.9 容器侧加固（2026-09-13 追加）

**① agent 工作区必须挂出来（否则重建即抹）**

容器内 agent 的会话 cwd 是 `/root/quantAI`（会话桶名 `--root-quantAI--`）。它原先在
**容器本地文件系统**里，`docker compose up -d dsh` 重建会连同 agent 正在用的文件一起抹掉。

**实录症状**（很容易误判成别的问题）：
- 文件读取报 `cannot read /root/quantAI/fetch_readme.mjs: not found`
- **bash 全线失败**：`Error: spawn …/landlock-run ENOENT`
  → 二进制其实**存在且可执行**（实测能跑）；`spawn` 报 ENOENT 的真因是 **cwd 不存在**。
  排查时别被路径带偏——先确认 cwd 在不在。

修法：`./dsh/root-quantAI:/root/quantAI`（对齐既有 `./dsh/root-dsh:/root/.dsh` 约定）。

**② 镜像缺 pnpm，插件装不了**

`dsh plugin add` 把参数转发给 profile 目录下的 pnpm。原镜像只有 npm/corepack，
容器内 agent 要装插件得先临时 bootstrap 一份 pnpm。
修法：`Dockerfile.dsh` 加 `ARG PNPM_VERSION=11.24.0` + `npm install -g pnpm@…`（与宿主同版本，别裸装）。

**③ dshmarket 的版本分界（原警告已修正）**

**`dshmarket@1.35.0` 在 0.1.5 下会让 profile 起不来**——它引用已不存在的
`installSettingsSection`，而一个坏插件会让整棵插件树加载失败（见 §4.2/§4.3）。
宿主 web profile 的 bundles 里正是 1.35.0，所以起不来。

**但 `1.45.1` 已修好**：实测（隔离 DSH_HOME + 真实 boot）启动行正常打出、无失败签名，
容器现装的就是它。`@xmanrui/dsh-im@4.20.0` 同样验证兼容 0.1.5-rc.1。

> 教训：**装插件前先验证再重启**。用隔离 DSH_HOME 复制一份 profile 到临时目录、
> 换个端口试启动，几十秒就能判断，不必拿运行中的实例赌。

**⑤ 灵枢记忆插件（@furongjun1999/dsh-memory）的三层依赖**

装了以后插件树能加载，但它的 `lingshu-bridge` 要 spawn python 子进程，**连踩三层**：

| 层 | 症状 | 修法 |
|---|---|---|
| 容器无 python | `[lingshu-bridge] spawn python ENOENT` + 8 次重试 | 镜像装 `python3` **和 `python-is-python3`**（它 spawn 的是 `python` 而非 `python3`） |
| 未给数据根 | `[mdcg-mcp] 缺少 MDCG_ROOT 环境变量` → 进程 0 秒退出 | compose 设 `MDCG_ROOT=/root/.dsh/mdcg`（插件默认值是 `data/mdcg`，**相对 cwd**，cwd 不稳定且落容器本地会丢） |
| 模块找不到 | `/usr/bin/python: No module named 'md_cg'` | compose 设 `PYTHONPATH=<插件目录>`：**它 spawn 时不设 cwd 也不设 PYTHONPATH**，而 md_cg 随插件装在 node_modules 下 |

**实测确认**：三层都补上后，`ModuleNotFoundError`/`code=1 退出`/`重试` 三条计数**全为 0**，
认知图目录在宿主 `dsh/root-dsh/mdcg/` 建出 `anchor`/`contextual`/`goals`/`hippocampus` 结构。

> 该插件的 md_cg 只 import 标准库（sqlite3/json/hashlib/…），**不需要 pip 包**，裸 python3 即可。

**⑥ 启动耗时随插件数增长**

2 个插件约 60s 绑定端口，6 个插件约 30-40s（并发加载）。**期间 8093 会返 502**，属正常窗口，
不是故障——判据是"启动行打印 + 直连 127.0.0.1:3081 返回 401"。

**④ 容器仍是"坏插件即不能启动"的形态**

§4.5 的守护启动器**尚未接到容器**。当时的理由是"容器无第三方插件"——
**该前提已失效**（现已装 `dsh-im`，且用户可能继续装插件）。
容器 `restart: unless-stopped` 在坏插件下只会**无限崩溃重启而不自愈**。
建议后续把守护启动器接进容器 entrypoint（需处理 PID 1 的信号转发）。

### 4.10 仍未做 / 遗留

| 项 | 状态 |
|---|---|
| **守护启动器接入容器** | 未做，见 §4.9④。前提已不成立，优先级应上调 |
| `scripts/start_dsh.sh` 的 DSH_BIND_IP 缺陷 | **仍未修**（先于本次升级）。修法：把 `DSH_BIND_IP` 移出 `.env` 改由启动脚本 export |
| 宿主 `web` profile 的坏插件 | bundles 里仍有 `dshmarket` 等，profile 起不来；宿主 web 目前无进程使用 |
| `Dockerfile.dsh` 的 `CMD` | 与 compose 的 `command` 不一致（被覆盖），已在文件内注释说明；两处改一处会踩坑 |
| 容器 `baymax-dsh-proxy` | 今日已重建（注入鉴权 cookie），回滚镜像 `baymax-dsh-proxy:pre-authcookies-rollback` |

---

## 5 · 验收总表

| 阶段 | 核心验收 | 可回滚 |
|---|---|---|
| 1 归因 | A/B 输出可解析率无回归；新增会话 100% 可归因 | ✅ 不传 job 即旧行为 |
| 2 读取器 | 376 会话回放与基线一致；重现 09-10 异常；v2/v3 双通过 | ✅ 纯只读，删产物即回退 |
| 3 判据 | 回放 09-03~09-12 正确标记 09-10；crontab 未动 | ✅ manifest 是新增文件 |
| 4 面板 | 只读视图可见三态；交易路径零改动 | ✅ 独立服务 |

## 6 · 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 阶段 1 改任务文本影响模型输出 | **实盘决策质量** | A/B 回归未过则不上线，改走 patch 注入 |
| 判据假阳性 | 告警疲劳 → 真故障被淹没 | 先只报不告警，人工核对 2-3 日 |
| 监控自身故障被当成"没干活" | 误判 | 监控自身心跳独立留痕，区分"没数据"与"没干活" |
| 容器升级引入行为变更 | **实盘前端/交易** | 独立验证 `baymax.cordis.yml`；钉 Dockerfile 版本；非交易日窗口 |
| v3 会话格式丢流失式 chunks | reasoning 不可回溯 | 阶段 2 明确契约字段；需要 reasoning 时另行评估 |

---

## 附：与既有路线图的关系

`docs/AGENT_ROADMAP.md` 的阶段 0-5 覆盖**交易智能体的能力演进**；本文档覆盖**其运行的可观测与治理**，
是前者的**基础设施层**，不改变任何交易逻辑与闸门。两者并行不冲突。
