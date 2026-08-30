"""
通达信代码格式校验与参数验证

企业级: 在下发通达信前校验参数, 快速失败, 避免无效请求到达客户端.
"""
import re

# 市场后缀 → 编号 (通达信内部格式)
SUFFIX_TO_MARKET = {
    "SZ": 0, "SH": 1, "BJ": 2, "US": 74, "HK": 31, "NQ": 44,
    "SZO": 9, "SHO": 8, "CSI": 62, "CNI": 102, "HG": 38,
    "CFF": 47, "SHF": 30, "DCE": 29, "CZC": 28, "GFE": 66,
    "HI": 27, "OF": 33, "CFFO": 7, "CZCO": 4, "DCEO": 5,
    "SHFO": 6, "GFEO": 67, "QHZ": 42,
}

_STOCK_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_INDEX_RE = re.compile(r"^\d{6}\.(CSI|CNI)$")


def check_stock_code_format(code: str) -> bool:
    """校验 A 股代码格式: 6位数字 + .SH/.SZ/.BJ"""
    if not code or not isinstance(code, str):
        return False
    return bool(_STOCK_RE.match(code.strip().upper()))


def normalize_stock_code(code: str) -> str:
    """把 600519 / 600519.SH 标准化为 600519.SH"""
    s = str(code or "").strip()
    if not s:
        return ""
    if "." in s:
        return s.upper()
    if s.startswith(("6", "9", "5")):
        return f"{s}.SH"
    return f"{s}.SZ"


def validate_params(method: str, params: dict) -> str | None:
    """校验方法参数, 返回错误信息 (None=通过)."""
    if not params:
        return None
    # 单股票方法: 校验代码格式
    single_stock_methods = ("get_stock_info", "get_more_info", "get_relation",
                            "get_exday_data", "get_kzz_info", "get_divid_factors",
                            "get_gb_info", "get_gb_info_by_date", "get_market_snapshot")
    if method in single_stock_methods:
        code = params.get("stock_code") or params.get("symbol")
        if code and not check_stock_code_format(code):
            return f"股票代码格式无效: {code} (需 6位数字.SH/.SZ/.BJ)"
    # 多股票方法
    multi_stock_methods = ("get_market_data", "get_zdt_data", "get_pricevol",
                           "get_financial_data", "get_gpjy_value")
    if method in multi_stock_methods:
        codes = params.get("stock_list") or []
        if codes:
            for c in codes:
                if not check_stock_code_format(c):
                    return f"股票代码格式无效: {c}"
    return None
