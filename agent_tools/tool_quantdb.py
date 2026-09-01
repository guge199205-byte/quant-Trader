"""只读 quantdb 查询工具（白名单 SQL）：agent 可查历史K线/估值/行业/新闻/信号。

连接 quantmind PG（QM_PG_* 环境变量，.env 提供，宿主 172.17.0.1:5432）。
只读约束（agent 提示词也写明）：
- 仅 SELECT 单语句（禁止 ; 拼接、注释绕过、多语句）
- 主表 stock_daily_latest（1077 万行：trade_date/open/high/low/close/volume/
  amount/pe_ttm/pb/roe/total_mv/turnover_rate/pct_change/is_st/ma5-60/
  beta_20/industry/stock_name/concept_*，symbol 前缀式 SH603018）
- 另支持 stock_daily_new_YYYY_MM 月分区K线表；新闻 news_article_enrichment；
  信号 engine_signal_scores；L2 因子 tdx_l2_daily
- 强制 LIMIT（无 LIMIT 自动补 100，上限 500）
- 每次查询独立短连接（8s statement_timeout），失败返回错误串不抛栈

用法（agent persona）：
- 历史K线: SELECT trade_date, open, close, volume FROM stock_daily_latest
  WHERE symbol='SH603018' ORDER BY trade_date DESC LIMIT 20
- 行业强度: SELECT industry, COUNT(*) FROM stock_daily_latest
  WHERE trade_date=(SELECT MAX(trade_date) FROM stock_daily_latest)
  AND pct_change IS NOT NULL GROUP BY industry ...
- 新闻: SELECT * FROM news_article_enrichment WHERE ...
"""

import json
import os
import re

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("QuantDB")

ALLOWED_TABLES = {
    # 主数据：全 A 日线+估值+均线+行业（1077 万行，symbol 前缀式 SH603018）
    "stock_daily_latest",
    "stocks",
    "stock_aliases",
    "stock_industry",
    "stock_tag",
    # 新闻 / 信号 / L2 因子 / 因子目录
    "news_article_enrichment",
    "engine_signal_scores",
    "tdx_l2_daily",
    "qm_quantdb_factor_field",
}
ALLOWED_TABLE_PATTERNS = (r"stock_daily_new_\d{4}_\d{2}",)  # 月分区K线表
MAX_LIMIT = 500
DEFAULT_LIMIT = 100
# 数值列太多会撑爆回复，默认排除高频噪声列
SKIP_COLUMNS = {"raw_json", "extras", "meta", "payload"}


def _conn():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("QM_PG_HOST", "127.0.0.1"),
        port=int(os.getenv("QM_PG_PORT", "5432")),
        dbname=os.getenv("QM_PG_DB", "quantmind"),
        user=os.getenv("QM_PG_USER", "quantmind"),
        password=os.getenv("QM_PG_PASSWORD", ""),
        connect_timeout=5,
        options="-c statement_timeout=8000",
    )


def _guard(sql: str) -> tuple[bool, str]:
    """白名单校验：单条 SELECT、表在名单内、强制 LIMIT。返回 (ok, 说明/修正后 SQL)。"""
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False, "SQL 为空"
    if ";" in s:
        return False, "禁止多语句（; 拼接）"
    if re.search(r"--|/\*", s):
        return False, "禁止注释"
    m = re.match(r"(?is)^\s*select\b", s)
    if not m:
        return False, "仅支持 SELECT"
    for t in ALLOWED_TABLES:
        if re.search(rf"\b{re.escape(t)}\b", s, re.I):
            break
    else:
        if not any(re.search(pat, s, re.I) for pat in ALLOWED_TABLE_PATTERNS):
            return False, f"表不在白名单: {sorted(ALLOWED_TABLES)}（另支持 stock_daily_new_YYYY_MM 分区表）"
    # 强制 LIMIT
    if not re.search(r"\blimit\s+\d+", s, re.I):
        s = f"{s} LIMIT {DEFAULT_LIMIT}"
    s = re.sub(r"(?i)\blimit\s+(\d+)", lambda m: f"LIMIT {min(int(m.group(1)), MAX_LIMIT)}", s)
    return True, s


@mcp.tool
def query_quantdb(sql: str) -> str:
    """只读查询 quantmind 数据库（白名单表：klines/板块/行业/因子/新闻）。

    Args:
        sql: SELECT 语句，单条，表必须在白名单，自动补 LIMIT（上限 500）。
    """
    ok, err_or_sql = _guard(sql)
    if not ok:
        return f"❌ {err_or_sql}"
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(err_or_sql)
        cols = [d[0] for d in cur.description or []]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return f"❌ 查询失败: {exc}"
    if not rows:
        return "（查询无结果）"
    # 去掉高频噪声列，控制回复体积
    slim = [{k: v for k, v in r.items() if k.lower() not in SKIP_COLUMNS} for r in rows]
    return json.dumps(slim, ensure_ascii=False, default=str)[:8000]


if __name__ == "__main__":
    port = int(os.getenv("QUANTDB_HTTP_PORT", "8105"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
