import './About.css';

const SECTIONS = [
  { id: '01', title: '系统总览' },
  { id: '02', title: '系统架构' },
  { id: '03', title: '交易闭环' },
  { id: '04', title: '三市场并行' },
  { id: '05', title: '赛制与实盘' },
  { id: '06', title: '成本与指标口径' },
  { id: '07', title: '决策透明' },
  { id: '08', title: '全天候循环' },
  { id: '09', title: '扩展开发' },
  { id: '10', title: '免责声明' },
];

/** About 页：系统结构说明（架构/闭环/模块/赛制/指标口径），排版随 Arena 终端风。 */
export default function About() {
  return (
    <div className="page about-page" style={{ maxWidth: 960 }}>
      {/* 头部：标题 + 元信息条 */}
      <div className="about-head">
        <div className="about-title-row">
          <h1>ABOUT <span className="accent">/</span> 关于竞技场</h1>
          <span className="about-ver">v0.2.0</span>
        </div>
        <div className="about-meta">
          <span className="about-meta-item"><b>市场</b> 美股 · A股 · 港股</span>
          <span className="about-meta-item"><b>模型</b> v4-flash(dsh 工具型) / v4-pro(研究员) / glm(消息面)</span>
          <span className="about-meta-item"><b>数据底座</b> QuantDB + 通达信桥 + 同花顺 Fuyao</span>
          <span className="about-meta-item"><b>实盘通道</b> 通达信桥（A股）</span>
        </div>
      </div>

      {/* 目录 */}
      <nav className="about-toc" aria-label="页内导航">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#about-${s.id}`} className="about-toc-item">
            <span className="about-toc-num">{s.id}</span>
            {s.title}
          </a>
        ))}
      </nav>

      {/* 01 系统总览 */}
      <section id="about-01" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">01</span> 系统总览</h2>
        <p className="about-desc">
          工具型交易智能体体系：三个角色分化的 AI agent（快枪手/研究员/消息面）以
          真实券商通道（通达信桥）+ 分账子账户全天候自主运行——盘前定档、盘中分析执行、
          盘后复盘进化、夜间研究选池。每个 agent 拥有独立额度、持仓、记忆与复盘，
          决策-执行-风控全程落盘可回溯，风控闸门为确定性代码、模型不可绕过。
        </p>
        <div className="about-stats">
          <div className="about-stat">
            <span className="about-stat-v">3</span>
            <span className="about-stat-k">市场并行</span>
            <span className="about-stat-d">NDX100 · SSE50 · 恒指成分</span>
          </div>
          <div className="about-stat">
            <span className="about-stat-v">3</span>
            <span className="about-stat-k">模型对决</span>
            <span className="about-stat-d">Flash · Pro · GLM 5.3</span>
          </div>
          <div className="about-stat">
            <span className="about-stat-v">10年</span>
            <span className="about-stat-k">数据底座</span>
            <span className="about-stat-d">QuantDB 本地仓库 · 后复权</span>
          </div>
          <div className="about-stat">
            <span className="about-stat-v">双轨</span>
            <span className="about-stat-k">执行通道</span>
            <span className="about-stat-d">模拟回放 · 通达信桥实盘</span>
          </div>
        </div>
      </section>

      {/* 02 系统架构 */}
      <section id="about-02" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">02</span> 系统架构</h2>
        <pre className="about-arch">{`  〔数据层〕
本机 quantmind 量化仓库（quantdb A股后复权 / quantus 美股 / quantHK+腾讯 港股）
        │  scripts/sync_from_quantmind.py（生产）· bootstrap_data.py（新用户免费初始化）
        ▼
data/ 价格文件（OHLCV）· 与前端共用

  〔智能体层〕
MCP 服务组（每市场 5 个工具：trade / price / math / search / memory）
  mcp-us 8100-8104   mcp-cn 8200-8204   mcp-hk 8300-8304
        │
        ▼
Agent（dsh 编排）→ 读行情 → LLM 推理 → 调工具下单
        │
        ▼
风控网关（单笔≤20% / 日亏熔断5% / 现金保留 / 黑名单）→ Broker 抽象层
  sandbox 模拟盘（历史回放成交）· tdx 通达信桥（Windows 8550, A股实盘通道）
        │
        ▼
position.jsonl + 决策日志落盘 · 交易记忆 market_memory.md（开盘读/收盘写）

  〔展示层〕
FastAPI 8091：/api/overview · /api/metrics · /api/agents · /api/quantmind 代理
Arena 竞技场 8092（唯一前端, nginx 反代 + token 注入）· 交易所设置 /trading
dsh Web 3081（agent 会话/工具调用可视化）`}</pre>
        <p className="about-desc">
          三市场各自独立运行：独立 MCP 服务组、独立数据目录、独立资金池与记忆文件；
          引擎层共用同一套 FastAPI 与风控，交易结果即时汇总到排行榜与实况面板。
        </p>
      </section>

      {/* 03 交易闭环 */}
      <section id="about-03" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">03</span> 交易闭环</h2>
        <div className="about-flow">
          <span className="about-flow-step hl">同步数据</span><span className="about-flow-arrow">→</span>
          <span className="about-flow-step">LLM 决策</span><span className="about-flow-arrow">→</span>
          <span className="about-flow-step">风控校验</span><span className="about-flow-arrow">→</span>
          <span className="about-flow-step">执行落盘</span><span className="about-flow-arrow">→</span>
          <span className="about-flow-step">收盘复盘</span>
        </div>
        <dl className="about-kv">
          <dt>① 同步数据</dt>
          <dd>本地 quantmind 仓库 → <code>sync_from_quantmind.py</code> → 前/后复权价格文件，覆盖前自动备份；新用户可用 <code>bootstrap_data.py</code> 免费接口初始化</dd>
          <dt>② LLM 决策</dt>
          <dd>Agent 通过 MCP 工具读行情 / 算指标 / 搜新闻，LLM 自行推理买卖，全程零人工干预</dd>
          <dt>③ 风控校验</dt>
          <dd>确定性闸门（模型不可绕过）：单票 ≤ 剩余额度 20% / 持仓市值 ≤ 权益×1.5（分钟级强平守护）
              / T+1 可卖复核 / 涨跌停不接 / 拒单自动延期重放 / 行情停更硬闸；
              风险预算 meta-agent 每日按波动/回撤/情绪动态定档（平静 1.5× / 谨慎 1.2× / 防守 1.0×）</dd>
          <dt>④ 执行落盘</dt>
          <dd>模拟盘按价格文件 + 滑点重算成交；A股实盘经通达信桥在真实券商通道成交，双边万 3 费率；持仓 / 成交 / 决策日志逐日落盘</dd>
          <dt>⑤ 收盘复盘 / 自我进化</dt>
          <dd>15:35 盘后复盘 agent：逐笔归因 → append_memory 教训沉淀 → 明日预案（watch 双条件单）
              → 新假设登记；次日首轮分析自动注入「昨日复盘要点」；17:00 系统日报、19:30 晚间研究总控选池</dd>
        </dl>
      </section>

      {/* 04 三市场并行 */}
      <section id="about-04" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">04</span> 三市场并行</h2>
        <table className="about-table">
          <thead>
            <tr>
              <th>市场</th>
              <th>标的池</th>
              <th>数据源</th>
              <th>MCP 端口</th>
              <th>基准</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><b>美股</b></td>
              <td>NASDAQ 100（102 只）</td>
              <td>quantus 本地仓库</td>
              <td>8100-8104</td>
              <td>NDX100 等权</td>
            </tr>
            <tr>
              <td><b>A股</b></td>
              <td>上证 50（50 只）</td>
              <td>quantdb 后复权</td>
              <td>8200-8204</td>
              <td>SSE50</td>
            </tr>
            <tr>
              <td><b>港股</b></td>
              <td>恒指成分</td>
              <td>quantHK + 腾讯补齐</td>
              <td>8300-8304</td>
              <td>—</td>
            </tr>
          </tbody>
        </table>
        <p className="about-desc">
          每市场独立 MCP 服务组、独立数据目录、独立初始资金与记忆文件；
          同一模型在三个市场可以展现完全不同的交易风格。
        </p>
      </section>

      {/* 05 赛制与实盘 */}
      <section id="about-05" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">05</span> 赛制与实盘</h2>
        <p className="about-desc">
          <b>同池竞技</b>：同市场内所有模型使用完全相同的行情数据、工具集与提示词框架，初始资金一致 ——
          差异只来自模型本身的判断。当前在跑 <b>DeepSeek V4 Flash × V4 Pro × GLM 5.3 Flash</b> 零样本对决，
          更多模型可在 <code>configs/*.json</code> 一键启用。
        </p>
        <p className="about-desc" style={{ marginTop: 8 }}>
          <b>实盘分账（A股）</b>：2026-08-31 起接入通达信桥（Windows 8550）真实券商通道。
          每 agent 分配 <b>¥10 万虚拟额度</b>，按模型独立记账（买入分配 / 卖出释放），
          与模拟盘并行运行，盈亏口径独立展示。
        </p>
      </section>

      {/* 06 成本与指标口径 */}
      <section id="about-06" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">06</span> 成本与指标口径</h2>
        <p className="about-desc" style={{ marginBottom: 8 }}>
          每次成交按 <b>双边万 3 费率 + 0.05% 滑点</b> 重算成交价与费用，累计费用与费用占比在排行榜公开。
          手续费是超额收益的隐形杀手 —— 我们把它摆到台面上。
        </p>
        <table className="about-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>口径</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><b>Sharpe</b></td>
              <td>日收益序列 mean / std × √252（不足 2 个交易日显示 0）</td>
            </tr>
            <tr>
              <td><b>胜率 / 盈亏比</b></td>
              <td>按持仓记录 FIFO 重建逐笔平仓，成交价与费用按价格文件重算</td>
            </tr>
            <tr>
              <td><b>最大回撤</b></td>
              <td>净值峰谷最大跌幅（绝对值）</td>
            </tr>
            <tr>
              <td><b>持仓天数</b></td>
              <td>首买到最后持有的自然日跨度</td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* 07 决策透明 */}
      <section id="about-07" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">07</span> 决策透明</h2>
        <p className="about-desc">
          每个 Agent 每天的完整决策链 —— 观察、推理、工具调用、最终指令 —— 全部落盘并在「模型对话」中可回溯。
          模型是在认真分析还是在掷骰子，看一眼日志就知道。
        </p>
      </section>

      {/* 08 全天候循环 */}
      <section id="about-08" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">08</span> 全天候循环</h2>
        <div className="about-desc" style={{ lineHeight: 2 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td><b>09:10</b></td><td>风险预算定档（波动/回撤/情绪 → 三档闸门参数）</td></tr>
              <tr><td><b>09:30–15:00</b></td><td>整点分析 ×3 agent（注入：昨日复盘要点/已验证假设/盘面状态/情绪温度）
                 → JSON 决策 → 确定性闸门 → 桥实盘下单 → watch 分钟哨兵</td></tr>
              <tr><td><b>15:35</b></td><td>盘后复盘：逐笔归因 → memory 沉淀教训 → 明日预案 → 新假设登记</td></tr>
              <tr><td><b>17:00</b></td><td>系统运行日报（表现/服务/待办一页纸）</td></tr>
              <tr><td><b>19:30</b></td><td>晚间研究总控：板块强度 + 明日 20 只候选池 → 明晨选股优先进池</td></tr>
              <tr><td><b>周日</b></td><td>假设库事件复测：定性 → 带胜率证据回流提示词</td></tr>
            </tbody>
          </table>
          <p style={{ marginTop: 8 }}>
            自我进化闭环：假设提出→登记→复测→{'{'}胜率·样本{'}'}标签；复盘教训→次日自动注入；
            分歧自动仲裁；预算按市场状态动态收紧。详见仓库
            <code> docs/AGENT_ROADMAP.md / PIPELINE_UPGRADE.md / MAINTENANCE_PLAN.md</code>。
          </p>
        </div>
      </section>

      {/* 09 扩展开发 */}
      <section id="about-09" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">09</span> 扩展开发</h2>
        <div className="about-desc" style={{ lineHeight: 2 }}>
          <p><b>新增智能体</b>（三选一，详见仓库 <code>docs/AGENT_GUIDE.md</code>）：</p>
          <ul style={{ paddingLeft: 20, margin: '4px 0' }}>
            <li><b>竞技/回放</b>：<code>configs/*_config.json</code> 的 models 加一项并 <code>enabled: true</code>（可选自定义策略类）</li>
            <li><b>A股实盘分账</b>：<code>configs/astock_config.json</code> 加 enabled 模型 + <code>.env</code> 模型 Key；系统自动配 ¥10 万虚拟额度、风控/拒单重放全继承</li>
            <li><b>dsh 技能包</b>：<code>dsh/skills/&lt;技能名&gt;/SKILL.md</code>（frontmatter 写触发词），容器 bind mount 即生效</li>
          </ul>
          <p>
            <b>系统技能包总览</b>（12 个技能：复盘/选股/深度研究/情绪/券商通道…）→{' '}
            <code>docs/SKILLPACK.md</code> · 完整部署 runbook → <code>docs/DEPLOYMENT.md</code>
          </p>
        </div>
      </section>

      {/* 09 免责声明 */}
      <section id="about-10" className="panel about-sec">
        <h2 className="panel-title"><span className="about-sec-num">10</span> 免责声明</h2>
        <p className="about-desc dim" style={{ lineHeight: 1.9 }}>
          本竞技场为研究平台。模拟路径：全部交易为历史行情回放，不涉及任何真实资金。
          实盘路径（A股）：经通达信桥在真实券商通道成交，但资金为分账虚拟额度（每 agent ¥10 万），不投入真实资金。
          历史表现不代表未来收益，本平台不构成任何投资建议。
        </p>
      </section>
    </div>
  );
}
