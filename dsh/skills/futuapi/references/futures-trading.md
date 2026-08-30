<!-- TOC: 期货交易命令（Futures） -->
# 期货交易命令（Futures）

期货交易完整文档（合约代码、账户、下单、持仓、撤单等）见 `docs/FUTURES_TRADING.md`。

## 期货交易命令

> 期货交易的完整文档（合约代码、账户查询、下单流程、持仓查询、撤单等）参见 `docs/FUTURES_TRADING.md`。

**核心要点**：期货必须使用 `OpenFutureTradeContext`（非 `OpenSecTradeContext`），现有交易脚本不适用于期货，需直接生成 Python 代码。常见 SG 期货主连代码：`SG.CNmain`(A50)、`SG.NKmain`(日经)。

---

---

**相关技能路由：** 相关：完整期货文档 → docs/FUTURES_TRADING.md；预测市场 → prediction-market.md。
