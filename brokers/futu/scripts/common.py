#!/usr/bin/env python3
"""
公共工具模块 - 提供 Futu OpenAPI 脚本的通用功能

包含：
- 环境变量配置
- 依赖检查与自动安装
- OpenD 连接配置
- 交易环境/市场枚举转换
- 上下文管理辅助
"""
import os
import subprocess
import sys
import json
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


# ============================================================
# 环境变量配置
# ============================================================

@dataclass
class FutuConfig:
    """Futu OpenAPI 配置类"""
    # 登录凭证
    login_account: Optional[str] = None
    login_pwd: Optional[str] = None

    # OpenD 连接配置
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111

    # 交易配置
    trd_env: str = "SIMULATE"
    default_market: str = "NONE"
    security_firm: Optional[str] = None


def get_config() -> FutuConfig:
    """
    获取 Futu OpenAPI 配置

    从环境变量中读取配置，未设置的使用默认值。

    环境变量:
        - FUTU_LOGIN_ACCOUNT: Futu 登录账号
        - FUTU_LOGIN_PWD: Futu 登录密码
        - FUTU_OPEND_HOST: OpenD 主机地址 (默认: 127.0.0.1)
        - FUTU_OPEND_PORT: OpenD 端口 (默认: 11111)
        - FUTU_TRD_ENV: 交易环境 (默认: SIMULATE)
        - FUTU_DEFAULT_MARKET: 默认市场 (默认: NONE)

    Returns:
        FutuConfig: 配置对象
    """
    return FutuConfig(
        login_account=os.getenv("FUTU_LOGIN_ACCOUNT", ""),
        login_pwd=os.getenv("FUTU_LOGIN_PWD", ""),
        opend_host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
        opend_port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        trd_env=os.getenv("FUTU_TRD_ENV", "SIMULATE"),
        default_market=os.getenv("FUTU_DEFAULT_MARKET", "NONE"),
        security_firm=os.getenv("FUTU_SECURITY_FIRM", "") or None,
    )


def _ensure_utf8_io():
    """Windows 下切换 stdout/stderr 为 UTF-8，避免 GBK 编码错误"""
    if sys.platform != "win32":
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


_ensure_utf8_io()


# ============================================================
# 依赖检查
# ============================================================

SDK_MODULE_NAME = "futu"  # 固定品牌模块名

# 与 install-futu-opend SKILL.md 的 metadata.version 保持一致
# 使用 /install-futu-opend 技能安装完 OpenD 后会自动生成版本戳文件
SKILL_VERSION = "0.1.1"
STAMP_FILE = os.path.join(os.path.expanduser("~"), ".futu_skill_version")

MIN_SDK_VERSION = "10.4.6408"
# OpenCryptoTradeContext 在 10.5.6508 才引入，加密货币脚本需要更高版本
MIN_CRYPTO_SDK_VERSION = "10.5.6508"

# ai_type 参数需要 SDK >= MIN_SDK_VERSION，低版本不传此参数
_sdk_supports_ai_type = True

# 环境检查缓存：首次完整检查后写入临时文件，TTL 内跳过
_ENV_CHECK_CACHE_FILE = os.path.join(tempfile.gettempdir(), ".futu_env_ok")
_ENV_CHECK_TTL = 3600  # 1 小时


def _parse_version(ver_str):
    """将版本字符串解析为可比较的元组，如 '10.4.6408' -> (10, 4, 6408)"""
    try:
        return tuple(int(x) for x in ver_str.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


def _env_check_is_cached():
    """检查是否有近期的成功环境检查缓存"""
    try:
        mtime = os.path.getmtime(_ENV_CHECK_CACHE_FILE)
        return (time.time() - mtime) < _ENV_CHECK_TTL
    except OSError:
        return False


def _env_check_mark_ok():
    """标记环境检查通过"""
    try:
        with open(_ENV_CHECK_CACHE_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _check_version_stamp():
    """检查版本戳文件，确保 OpenD 和 SDK 已通过 install skill 正确安装（仅警告，不阻断）"""
    try:
        with open(STAMP_FILE, "r", encoding="utf-8") as f:
            installed = f.read().strip()
    except FileNotFoundError:
        print(f"[WARN] 未检测到版本戳文件 {STAMP_FILE}，建议运行 /install-futu-opend 安装", file=sys.stderr)
        return
    if installed != SKILL_VERSION:
        print(f"[WARN] 版本戳不匹配: 已安装 {installed}, 当前要求 {SKILL_VERSION}，建议运行 /install-futu-opend 更新", file=sys.stderr)


def _check_opend_reachable():
    """检查 OpenD 是否可连接"""
    import socket
    config = get_config()
    host, port = config.opend_host, config.opend_port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
    except (ConnectionRefusedError, OSError) as e:
        print(f"[ERROR] 无法连接 OpenD ({host}:{port}): {e}")
        print("请先启动 OpenD 客户端")
        sys.exit(1)
    finally:
        sock.close()


def _detect_ai_type_support():
    """检测当前 SDK 是否支持 ai_type 参数（仅检查版本，不打印警告）"""
    global _sdk_supports_ai_type
    try:
        import futu
        current = getattr(futu, "__version__", "0")
        if _parse_version(current) < _parse_version(MIN_SDK_VERSION):
            _sdk_supports_ai_type = False
    except ImportError:
        pass


def ensure_futu_api():
    """环境检查（带缓存）：SDK 版本 + 版本戳 + OpenD 连通性。首次完整检查，TTL 内跳过。"""
    # 1. 缓存命中时仅做轻量 ai_type 支持检测
    if _env_check_is_cached():
        _detect_ai_type_support()
        return True

    # 2. 版本戳检查
    _check_version_stamp()

    # 3. SDK 导入 + 版本检查
    global _sdk_supports_ai_type
    try:
        import futu
        current = getattr(futu, "__version__", "0")
        if _parse_version(current) < _parse_version(MIN_SDK_VERSION):
            _sdk_supports_ai_type = False
            print(f"[WARN] futu-api 版本过低: {current} < {MIN_SDK_VERSION}，ai_type 参数将被跳过，建议运行 /install-futu-opend 升级 SDK", file=sys.stderr)
    except ImportError:
        print("[ERROR] futu-api 未安装，请运行 /install-futu-opend 安装")
        sys.exit(1)

    # 4. OpenD 连通性检查
    _check_opend_reachable()

    # 5. 标记检查通过
    _env_check_mark_ok()
    return True

ensure_futu_api()

from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        RET_OK,
        TrdEnv,
        TrdMarket,
        TrdSide,
        OrderType,
        ModifyOrderOp,
        SubType,
        Session,
        KLType,
        AuType,
        Market,
        SecurityType,
        SecurityReferenceType,
        SimpleFilter,
        AccumulateFilter,
        FinancialFilter,
        FinancialQuarter,
        StockField,
        SortDir,
        Plate,
        OrderBookType,
)

# 预测市场枚举与数据类（SDK >= 支持版本才提供，低版本降级为 None）
try:
    from futu import (
        ECStatus,
        ECContractType,
        ECFrequency,
        ECMilestoneType,
        ECKlineSource,
        PredSide,
        ComboLeg,
    )
except ImportError:
    ECStatus = None
    ECContractType = None
    ECFrequency = None
    ECMilestoneType = None
    ECKlineSource = None
    PredSide = None
    ComboLeg = None

# 预测市场推送 Handler（同样按版本降级）
try:
    from futu import (
        EventContractKlineHandlerBase,
        EventContractOrderBookHandlerBase,
        EventContractTickerHandlerBase,
    )
except ImportError:
    EventContractKlineHandlerBase = None
    EventContractOrderBookHandlerBase = None
    EventContractTickerHandlerBase = None

try:
    from futu import TradeDateMarket
except ImportError:
    TradeDateMarket = None

from futu import SecurityFirm

try:
    from futu import OpenCryptoTradeContext
except ImportError:
    OpenCryptoTradeContext = None

try:
    from futu import OpenFutureTradeContext
except ImportError:
    OpenFutureTradeContext = None

try:
    from futu import TimeInForce
except ImportError:
    TimeInForce = None

try:
    from futu import PredSide
except ImportError:
    PredSide = None


# ============================================================
# 连接配置
# ============================================================

def get_opend_config():
    """获取 OpenD 连接配置 -> (host, port)"""
    config = get_config()
    return config.opend_host, config.opend_port


def _check_opend_alive(host, port):
    """快速检测 OpenD 端口是否可连接，不可连接时直接报错退出"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        print(f"错误: 无法连接 OpenD ({host}:{port})，连接被拒绝。请先启动 OpenD 客户端。")
        sys.exit(1)
    except OSError as e:
        print(f"错误: 无法连接 OpenD ({host}:{port}): {e}。请检查 OpenD 是否已启动。")
        sys.exit(1)
    finally:
        sock.close()


def create_quote_context():
    """创建行情上下文"""
    host, port = get_opend_config()
    _check_opend_alive(host, port)
    kwargs = dict(host=host, port=port)
    if _sdk_supports_ai_type:
        kwargs["ai_type"] = 1
    return OpenQuoteContext(**kwargs)


def parse_security_firm(firm_str):
    """解析券商标识字符串 -> SecurityFirm 枚举，无效时返回 None"""
    if not firm_str:
        return None
    key = str(firm_str).strip().upper()
    if hasattr(SecurityFirm, key):
        return getattr(SecurityFirm, key)
    return None


def get_default_security_firm():
    """获取默认券商标识（从环境变量）"""
    config = get_config()
    return parse_security_firm(config.security_firm)


def create_trade_context(market=None, security_firm=None):
    """创建交易上下文"""
    host, port = get_opend_config()
    _check_opend_alive(host, port)
    trd_market = parse_market(market) if market else get_default_market()
    kwargs = dict(host=host, port=port, filter_trdmarket=trd_market)
    if _sdk_supports_ai_type:
        kwargs["ai_type"] = 1
    if security_firm is not None:
        kwargs["security_firm"] = security_firm
    else:
        default_firm = get_default_security_firm()
        kwargs["security_firm"] = default_firm if default_firm is not None else SecurityFirm.NONE
    return OpenSecTradeContext(**kwargs)


def create_future_trade_context(security_firm=None):
    """创建期货交易上下文（预测市场 / 期货下单使用，无 filter_trdmarket）"""
    if OpenFutureTradeContext is None:
        raise RuntimeError("当前 SDK 不支持 OpenFutureTradeContext，请升级 futu-api")
    host, port = get_opend_config()
    _check_opend_alive(host, port)
    kwargs = dict(host=host, port=port)
    if _sdk_supports_ai_type:
        kwargs["ai_type"] = 1
    if security_firm is not None:
        kwargs["security_firm"] = security_firm
    else:
        default_firm = get_default_security_firm()
        kwargs["security_firm"] = default_firm if default_firm is not None else SecurityFirm.NONE
    return OpenFutureTradeContext(**kwargs)


def is_event_contract_code(code):
    """预测市场代码以 EC. 开头（无市场前缀）"""
    return bool(code) and str(code).strip().upper().startswith("EC.")


TRADE_CTX_TYPE_CHOICES = ("SEC", "FUTURE")


def normalize_trade_ctx_type(ctx_type=None, code=None, codes=None):
    """解析交易上下文类型：SEC=证券，FUTURE=期货/预测市场。

    任一 EC. 代码一律走 FUTURE；其余按显式 ctx_type（默认 SEC）。
    """
    has_ec = bool(code and is_event_contract_code(code))
    if not has_ec and codes:
        for c in codes:
            code_val = c if isinstance(c, str) else getattr(c, "code", None)
            if code_val and is_event_contract_code(code_val):
                has_ec = True
                break
    if has_ec:
        return "FUTURE"
    if ctx_type is not None and str(ctx_type).strip() != "":
        key = str(ctx_type).strip().upper()
        if key in ("FUTURE", "FUTURES"):
            return "FUTURE"
        if key == "SEC":
            return "SEC"
        raise ValueError(f"无效 ctx_type: {ctx_type}，应为 SEC 或 FUTURE")
    return "SEC"


def create_sec_or_future_trade_context(market=None, security_firm=None, ctx_type="SEC", code=None, codes=None):
    """按 ctx_type / 代码创建证券或期货交易上下文（与 get_accounts 的 ctx_type 一致）"""
    resolved = normalize_trade_ctx_type(ctx_type=ctx_type, code=code, codes=codes)
    if resolved == "FUTURE":
        return create_future_trade_context(security_firm=security_firm)
    return create_trade_context(market, security_firm=security_firm)


# 加密货币交易对象仅支持 FUTUSECURITIES(香港)、FUTUINC(美国)、FUTUSG(新加坡)
CRYPTO_SUPPORTED_FIRMS = ("FUTUSECURITIES", "FUTUINC", "FUTUSG")

# 加密货币 firm 自动探测缓存：~/.futu_crypto_firm，TTL 内复用避免每次都遍历
_CRYPTO_FIRM_CACHE_FILE = os.path.join(tempfile.gettempdir(), ".futu_crypto_firm")
_CRYPTO_FIRM_CACHE_TTL = 3600


def _crypto_firm_cache_read():
    try:
        if (time.time() - os.path.getmtime(_CRYPTO_FIRM_CACHE_FILE)) >= _CRYPTO_FIRM_CACHE_TTL:
            return None
        with open(_CRYPTO_FIRM_CACHE_FILE, "r", encoding="utf-8") as f:
            name = f.read().strip()
        return name if name in CRYPTO_SUPPORTED_FIRMS else None
    except OSError:
        return None


def _crypto_firm_cache_write(firm_name):
    try:
        with open(_CRYPTO_FIRM_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(firm_name)
    except OSError:
        pass


def _crypto_firm_cache_clear():
    try:
        os.remove(_CRYPTO_FIRM_CACHE_FILE)
    except OSError:
        pass


def _build_crypto_ctx(firm_enum, host, port):
    kwargs = dict(host=host, port=port, security_firm=firm_enum)
    if _sdk_supports_ai_type:
        import inspect as _inspect
        try:
            if "ai_type" in _inspect.signature(OpenCryptoTradeContext).parameters:
                kwargs["ai_type"] = 1
        except (TypeError, ValueError):
            pass
    return OpenCryptoTradeContext(**kwargs)


def _probe_crypto_firm(firm, host, port):
    """探测指定券商是否有可用 CRYPTO 账户，True 表示有效。"""
    ctx = None
    try:
        ctx = _build_crypto_ctx(firm, host, port)
        ret, data = ctx.get_acc_list()
        return ret == RET_OK and not is_empty(data)
    except Exception:
        return False
    finally:
        safe_close(ctx)


def _detect_crypto_firm(host, port):
    """未显式指定 firm 时，遍历 CRYPTO_SUPPORTED_FIRMS 查找有 CRYPTO 账户的券商。

    缓存命中时仍需要 probe 一次，避免 OpenD 切换账号后命中过期券商。
    """
    cached = _crypto_firm_cache_read()
    if cached is not None:
        firm = getattr(SecurityFirm, cached, None)
        if firm is not None and _probe_crypto_firm(firm, host, port):
            return firm
        _crypto_firm_cache_clear()

    for name in CRYPTO_SUPPORTED_FIRMS:
        firm = getattr(SecurityFirm, name, None)
        if firm is None:
            continue
        if _probe_crypto_firm(firm, host, port):
            _crypto_firm_cache_write(name)
            return firm
    return None


def create_crypto_trade_context(security_firm=None):
    """创建加密货币交易上下文（OpenCryptoTradeContext）。

    security_firm 仅支持 FUTUSECURITIES、FUTUINC、FUTUSG。
    未指定时按优先级：参数 > FUTU_SECURITY_FIRM > 自动探测有 CRYPTO 账户的券商。
    """
    if OpenCryptoTradeContext is None:
        try:
            import futu as _futu
            cur = getattr(_futu, "__version__", "unknown")
        except ImportError:
            cur = "unknown"
        print(f"错误: 当前 futu-api {cur} 未提供 OpenCryptoTradeContext，加密货币需要 >= {MIN_CRYPTO_SDK_VERSION}。"
              f"请运行: pip install --upgrade \"futu-api>={MIN_CRYPTO_SDK_VERSION}\"")
        sys.exit(1)
    host, port = get_opend_config()
    _check_opend_alive(host, port)

    firm_enum = security_firm
    if firm_enum is None:
        firm_enum = get_default_security_firm()
    if firm_enum is None:
        firm_enum = _detect_crypto_firm(host, port)
    use_json = "--json" in sys.argv
    if firm_enum is None:
        msg = (f"未在 {', '.join(CRYPTO_SUPPORTED_FIRMS)} 下找到加密货币账户。"
               f"请确认已开通加密货币交易，或显式指定 --security-firm。")
        if use_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    firm_name = format_enum(firm_enum)
    if firm_name not in CRYPTO_SUPPORTED_FIRMS:
        msg = f"加密货币交易仅支持 {', '.join(CRYPTO_SUPPORTED_FIRMS)}，当前 security_firm={firm_name}"
        if use_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"错误: {msg}")
        sys.exit(1)

    return _build_crypto_ctx(firm_enum, host, port)


# ============================================================
# 枚举转换
# ============================================================

def parse_trd_env(env_str):
    """解析交易环境字符串 -> TrdEnv"""
    if env_str and str(env_str).upper() == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE


def parse_market(market_str):
    """解析交易市场字符串 -> TrdMarket"""
    if not market_str:
        return TrdMarket.US
    mapping = {
        "NONE": TrdMarket.NONE,
        "N/A": TrdMarket.NONE,
        "US": TrdMarket.US,
        "HK": TrdMarket.HK,
        "CN": TrdMarket.CN,
        "HKCC": TrdMarket.HKCC,
        "SG": TrdMarket.SG,
    }
    if hasattr(TrdMarket, "MY"):
        mapping["MY"] = TrdMarket.MY
    if hasattr(TrdMarket, "JP"):
        mapping["JP"] = TrdMarket.JP
    if hasattr(TrdMarket, "CRYPTO"):
        mapping["CRYPTO"] = TrdMarket.CRYPTO
        mapping["CC"] = TrdMarket.CRYPTO
    return mapping.get(str(market_str).upper(), TrdMarket.US)


# 交易脚本 CLI --market 可选值（SG/MY/JP 需 SDK 提供对应 TrdMarket 枚举）
TRD_MARKET_CLI_CHOICES = ["US", "HK", "HKCC", "CN", "SG", "MY", "JP"]


# 股票代码前缀 -> 交易市场映射
_CODE_PREFIX_TO_MARKET = {
    "US": "US",
    "HK": "HK",
    "SH": "CN",
    "SZ": "CN",
    "SG": "SG",
    "MY": "MY",
    "JP": "JP",
    "CC": "CRYPTO",
}


def is_crypto_code(code):
    """判断是否为加密货币代码（前缀 CC.）"""
    if not code or "." not in code:
        return False
    return code.split(".")[0].upper() == "CC"


def parse_qty(qty_str, code=None):
    """解析订单数量：加密货币支持小数，其他市场仅整数。

    - 加密货币（CC. 前缀）：允许浮点数
    - 其他：必须为正整数，否则抛出 ValueError
    """
    if qty_str is None:
        raise ValueError("数量不能为空")
    try:
        val = float(qty_str)
    except (TypeError, ValueError):
        raise ValueError(f"数量格式错误: {qty_str}")
    if val <= 0:
        raise ValueError("数量必须为正数")
    if is_crypto_code(code):
        return val
    if float(val).is_integer():
        return int(val)
    raise ValueError(f"非加密货币订单数量必须为整数，收到: {qty_str}")


def infer_market_from_code(code):
    """从股票代码前缀推导交易市场字符串，如 US.AAPL -> 'US'，HK.00700 -> 'HK'。
    无法识别时返回 None。"""
    if not code or "." not in code:
        return None
    prefix = code.split(".")[0].upper()
    return _CODE_PREFIX_TO_MARKET.get(prefix)


def parse_trd_side(side_str):
    """解析交易方向字符串 -> TrdSide"""
    if not side_str or str(side_str).strip().upper() not in ("BUY", "SELL"):
        raise ValueError(f"无效的交易方向: {side_str}，必须为 BUY 或 SELL")
    if str(side_str).strip().upper() == "BUY":
        return TrdSide.BUY
    return TrdSide.SELL


def parse_subtypes(subtype_names):
    """将字符串列表转换为 SubType 枚举列表"""
    subtypes = []
    for name in subtype_names:
        key = str(name).strip().upper()
        if key == "BASIC":
            key = "QUOTE"
        if not hasattr(SubType, key):
            raise ValueError(f"不支持的订阅类型: {name}")
        subtypes.append(getattr(SubType, key))
    return subtypes


# ============================================================
# 预测市场辅助
# ============================================================

# 预测市场 K 线仅支持这 4 种 KLType
EC_KLTYPE_CHOICES = ["K_1M", "K_5M", "K_60M", "K_DAY"]


def _ec_support_error():
    """返回当前 SDK 不支持预测市场的错误提示"""
    try:
        import futu as _futu
        cur = getattr(_futu, "__version__", "unknown")
    except ImportError:
        cur = "unknown"
    return (
        f"当前 futu-api {cur} 不支持预测市场，"
        "请运行: pip install --upgrade futu-api 升级至支持版本后重试"
    )


def assert_event_contract_support(ctx=None, output_json=None):
    """检测当前 SDK 是否支持预测市场接口，不支持则打印提示并退出。

    :param ctx: 已创建的行情上下文（用于检测方法是否存在），None 时检测枚举导入
    :param output_json: 是否以 JSON 格式输出错误（None 时按 --json 参数判断）
    """
    supported = (ECStatus is not None) and (ComboLeg is not None)
    if ctx is not None:
        supported = supported and hasattr(ctx, "get_event_contract_category")
    if supported:
        return
    if output_json is None:
        try:
            output_json = "--json" in sys.argv
        except Exception:
            output_json = False
    msg = _ec_support_error()
    if output_json:
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(f"错误: {msg}")
    sys.exit(1)


def parse_pred_side(name):
    """解析预测市场方向字符串 -> PredSide 枚举（YES/NO），不支持时抛 ValueError"""
    assert_event_contract_support()
    if name is None or str(name).strip() == "":
        return None
    key = str(name).strip().upper()
    if not hasattr(PredSide, key):
        raise ValueError(f"无效的合约方向: {name}，必须为 YES 或 NO")
    return getattr(PredSide, key)


def parse_ec_kline_source(name):
    """解析预测市场 K 线来源字符串 -> ECKlineSource 枚举（ORDER_BOOK_YES），None 返回 None"""
    assert_event_contract_support()
    if name is None or str(name).strip() == "":
        return None
    key = str(name).strip().upper()
    if not hasattr(ECKlineSource, key):
        raise ValueError(f"无效的 K 线来源: {name}，可选: ORDER_BOOK_YES")
    return getattr(ECKlineSource, key)


def parse_ec_status(name):
    """解析预测市场状态字符串 -> ECStatus 枚举（如 EVENT_ACTIVE），None 返回 None"""
    assert_event_contract_support()
    if name is None or str(name).strip() == "":
        return None
    key = str(name).strip().upper()
    if not hasattr(ECStatus, key):
        raise ValueError(f"无效的事件状态: {name}，可选: " + ECStatus.get_all_keys())
    return getattr(ECStatus, key)


def ensure_event_contract_subscribed(ctx, code, sub_type, output_json=None,
                                     kline_source_list=None, action="订阅预测市场"):
    """订阅预测市场指定类型，已订阅则静默跳过，其他失败打印错误并退出。

    供 get_event_contract_order_book/kline/ticker
    查询前自动订阅复用，避免重复的订阅逻辑块。

    :param ctx: 已创建的行情上下文
    :param code: 预测市场合约代码
    :param sub_type: SubType 枚举（ORDER_BOOK/TICKER/K_*）
    :param output_json: 是否以 JSON 格式输出错误（None 时按 --json 参数判断）
    :param kline_source_list: K 线来源列表（ECKlineSource 枚举），订阅 K 线类型时透传，
                              与查询时的 kline_source 保持一致；非 K 线类型不传
    :param action: 错误提示中的操作名
    """
    if output_json is None:
        try:
            output_json = "--json" in sys.argv
        except Exception:
            output_json = False
    kwargs = {}
    if kline_source_list:
        kwargs["kline_source_list"] = kline_source_list
    ret, err = ctx.subscribe_event_contract([code], [sub_type], **kwargs)
    if ret == RET_OK:
        return
    # 已订阅不致命；真正的权限/账户问题由后续 check_ret 兜底
    if "already" in str(err).lower() or "已订阅" in str(err):
        return
    if output_json:
        print(json.dumps({"error": f"{action}失败: {err}"}, ensure_ascii=False))
    else:
        print(f"{action}失败: {err}")
    sys.exit(1)


# ============================================================
# 配置获取
# ============================================================

def get_default_acc_id():
    """获取默认账户 ID"""
    return int(os.getenv("FUTU_ACC_ID", "0"))


def get_default_trd_env():
    """获取默认交易环境"""
    config = get_config()
    return parse_trd_env(config.trd_env)


def get_default_market():
    """获取默认交易市场"""
    config = get_config()
    return parse_market(config.default_market)



# ============================================================
# 数据处理辅助
# ============================================================

def safe_get(row, *keys, default=""):
    """安全获取 DataFrame 行或字典中的值，支持多个备选 key"""
    for key in keys:
        val = row.get(key) if hasattr(row, 'get') else getattr(row, key, None)
        if val is not None:
            return val
    return default


def safe_float(val, default=0.0):
    """安全转换为 float，遇到 N/A、空串、None 等非数值时返回默认值"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0):
    """安全转换为 int，遇到 N/A、空串、None 等非数值时返回默认值。
    处理 numpy 标量类型，避免大整数（如 18 位 acc_id）经 float64 转换后精度丢失。"""
    if val is None:
        return default
    # numpy 标量: 提取原生 Python 类型，避免 float64 精度丢失
    if hasattr(val, 'item'):
        val = val.item()
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default


def format_enum(val):
    """格式化枚举值为字符串"""
    if hasattr(val, "name"):
        return val.name
    return str(val)



def disp_width(s):
    """计算字符串在终端的显示宽度（中日韩全角=2，其他=1，σ等 Ambiguous 视为 1）。"""
    from unicodedata import east_asian_width
    return sum(2 if east_asian_width(c) in ("F", "W") else 1 for c in str(s))


def pad_disp(s, width, align="left"):
    """按显示宽度补空格对齐。"""
    s = str(s)
    pad = max(0, width - disp_width(s))
    if align == "right":
        return " " * pad + s
    return s + " " * pad


# ============================================================
# 上下文管理辅助
# ============================================================

def safe_close(ctx):
    """安全关闭上下文"""
    try:
        if ctx:
            ctx.close()
    except Exception:
        pass


def _is_no_account_error(error_msg):
    """检测错误信息是否为"无可用交易账户"（区别于行情权限不足）"""
    msg = str(error_msg).lower()
    keywords = [
        "no available real accounts",
        "no available accounts",
        "no available simulate accounts",
        "无可用账户", "无可用交易账户", "没有可用账户",
    ]
    return any(kw in msg for kw in keywords)


def _is_unlock_needed_error(error_msg):
    """检测错误信息是否为"交易未解锁"。"""
    msg = str(error_msg).lower()
    keywords = [
        "没有解锁交易", "请先解锁交易", "未解锁交易", "交易未解锁",
        "unlock needed", "trade not unlocked", "trade unlock",
        "please unlock", "need unlock",
    ]
    return any(kw in msg for kw in keywords)


def _is_permission_error(error_msg):
    """检测错误信息是否为行情权限不足。账户类错误优先匹配 _is_no_account_error。"""
    if _is_no_account_error(error_msg) or _is_unlock_needed_error(error_msg):
        return False
    keywords = [
        "权限", "没有权限", "权限不足", "无权限",
        "no permission", "permission denied", "not permission",
        "authority", "no authority",
        "quota", "no quota",
        "bmp", "lv1", "lv2",
        "未开通", "未购买", "need subscribe",
        "not subscribed", "unsubscribed",
    ]
    msg = str(error_msg).lower()
    return any(kw in msg for kw in keywords)


_MARKET_NAMES = {
    "HK": "港股", "US": "美股",
    "SH": "A股", "SZ": "A股",
    "SG": "新加坡",
    "MY": "马股",
    "JP": "日股",
}

_AUTHORITY_URLS = {"futu": "https://openapi.futunn.com/futu-api-doc/intro/authority.html"}


def _detect_market_from_argv():
    """从命令行参数中的股票代码检测市场（如 HK.00700 -> 港股）"""
    import re
    for arg in sys.argv[1:]:
        m = re.match(r'^(HK|US|SH|SZ|SG|MY|JP)\.', arg, re.IGNORECASE)
        if m:
            return _MARKET_NAMES.get(m.group(1).upper(), "")
    return ""


def _get_authority_url():
    """返回行情权限页面链接"""
    return _AUTHORITY_URLS["futu"]



def _build_permission_hint():
    """构建行情权限不足的提示信息"""
    market = _detect_market_from_argv()
    market_prefix = f"{market}" if market else ""
    url = _get_authority_url()
    return (
        f"\n\n{market_prefix}行情权限不足，请购买对应行情卡以获取数据。"
        f"详情参考：{url}"
    )



def _build_permission_hint_json():
    """构建 JSON 格式的行情权限提示字段"""
    market = _detect_market_from_argv()
    market_prefix = f"{market}" if market else ""
    hint = f"{market_prefix}行情权限不足，请购买对应行情卡以获取数据"
    url = _get_authority_url()
    return {"hint": hint, "authority_url": url}



_NO_ACCOUNT_HINT = (
    "未找到可用交易账户。常见原因：1) 该 security_firm 下未开通对应市场的交易账户；"
    "2) OpenD 登录的账号与目标账户不一致；3) 加密货币账户需先在富途/Moomoo APP 中开通。"
    "可调整 --security-firm / FUTU_SECURITY_FIRM 或 FUTU_TRD_ENV 后重试。"
)

_UNLOCK_NEEDED_HINT = (
    "实盘交易未解锁。请在 OpenD GUI 界面点击「解锁交易」并输入交易密码完成解锁，"
    "解锁后重新执行下单/撤单。出于安全考虑，本技能不通过 SDK 调用 unlock_trade。"
)


def check_ret(ret, data, ctx=None, action="操作", output_json=None):
    """检查 API 返回值，失败则打印错误并退出"""
    if ret != RET_OK:
        if output_json is None:
            try:
                output_json = "--json" in sys.argv
            except Exception:
                output_json = False

        no_acc_error = _is_no_account_error(data)
        unlock_error = (not no_acc_error) and _is_unlock_needed_error(data)
        perm_error = (not no_acc_error) and (not unlock_error) and _is_permission_error(data)

        if output_json:
            err_obj = {"ret": ret, "action": action, "error": str(data)}
            if no_acc_error:
                err_obj["hint"] = _NO_ACCOUNT_HINT
            elif unlock_error:
                err_obj["hint"] = _UNLOCK_NEEDED_HINT
            elif perm_error:
                err_obj.update(_build_permission_hint_json())
            print(json.dumps(err_obj, ensure_ascii=False))
        else:
            print(f"{action}失败: {data}")
            if no_acc_error:
                print(f"\n{_NO_ACCOUNT_HINT}")
            elif unlock_error:
                print(f"\n{_UNLOCK_NEEDED_HINT}")
            elif perm_error:
                print(_build_permission_hint())
        safe_close(ctx)
        sys.exit(1)


def is_empty(data):
    """检查数据是否为空"""
    if data is None:
        return True
    if hasattr(data, "shape"):
        return data.shape[0] == 0
    if hasattr(data, "__len__"):
        return len(data) == 0
    return False


def to_jsonable(val, default=None):
    """将值转为 JSON 可序列化类型"""
    import math
    if val is None:
        return default
    if hasattr(val, "item"):
        val = val.item()
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return default
    if hasattr(val, "name"):
        return val.name
    if isinstance(val, (str, int, float, bool, list, dict)):
        return val
    return str(val)


def df_to_records(df, limit=None):
    """将 DataFrame 转为 JSON 可序列化的记录列表"""
    if is_empty(df):
        return []
    total = len(df)
    n = total if (limit is None or limit <= 0) else min(total, limit)
    records = []
    for i in range(n):
        row = df.iloc[i] if hasattr(df, "iloc") else df[i]
        if hasattr(row, "index"):
            keys = row.index
        elif isinstance(row, dict):
            keys = row.keys()
        else:
            keys = [k for k in dir(row) if not k.startswith("_")]
        records.append({
            k: to_jsonable(row.get(k) if hasattr(row, "get") else getattr(row, k, None))
            for k in keys
        })
    return records


def print_display_df(df, max_colwidth=26):
    """终端展示：东亚字符宽度 + DataFrame.to_string（pandas 3 须将 max_colwidth 传给 to_string）。"""
    import pandas as pd

    if df is None or is_empty(df):
        print("无数据")
        return
    with pd.option_context(
        "display.unicode.east_asian_width", True,
        "display.width", None,
        "display.max_rows", None,
    ):
        print(df.to_string(index=False, max_colwidth=max_colwidth))


def scale_int(value, scale_pow10: int, display_decimals: int = None) -> str:
    """将放大后的整型字段按 10 的幂次还原为带小数点的字符串，避免浮点精度丢失。

    示例：scale_int(552000000000, 9)    → "552.000000000"
          scale_int(552000000000, 9, 3) → "552.000"
          scale_int(35804, 3)           → "35.804"

    :param value: 原始 proto int64 值（可以是 int 或可转 int 的类型）
    :param scale_pow10: 放大倍数的幂次，即实际值 = value / 10**scale_pow10
    :param display_decimals: 展示小数位数（默认与 scale_pow10 一致；传入较小值可截断尾部零）
    :return: 格式化字符串，保留指定位数小数
    """
    from decimal import Decimal, ROUND_DOWN
    if value is None:
        return "-"
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return "-"
    d = Decimal(iv)
    divisor = Decimal(10 ** scale_pow10)
    result = d / divisor
    # 确定展示精度
    decimals = display_decimals if display_decimals is not None else scale_pow10
    fmt = "0." + "0" * decimals if decimals > 0 else "0"
    return str(result.quantize(Decimal(fmt), rounding=ROUND_DOWN))


def format_big_number(n, *, fixed: int = 2) -> str:
    """将大整数按万/亿/万亿单位缩放，返回带单位后缀的字符串。
    任意列存在 ≥ 10000 的值时，整列应统一调用本函数（--json 路径不调用）。

    示例：format_big_number(1234567890000) → "1.23万亿"
          format_big_number(12345678900)   → "123.46亿"
          format_big_number(12345)         → "1.23万"
          format_big_number(999)           → "999"

    :param n: 原始数值（int/float）
    :param fixed: 保留小数位数（默认 2）
    :return: 格式化字符串
    """
    if n is None:
        return "-"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "-"
    import math
    if math.isnan(v) or math.isinf(v):
        return "-"
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}万亿"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f}万"
    return str(int(v)) if v == int(v) else f"{v:.{fixed}f}"


