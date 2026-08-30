"""BayMax-Trader 后端配置加载。

从 config/backend.yaml 读取后端配置，并合并 .env 中的密钥引用。
使用 yaml.safe_load 保证安全加载。
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "backend.yaml"


@lru_cache(maxsize=1)
def load_backend_config(config_path: str | os.PathLike | None = None) -> dict:
    """Load backend.yaml (cached)."""
    load_dotenv(PROJECT_ROOT / ".env")
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"后端配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config: dict) -> None:
    """Merge broker env vars into broker config."""
    brokers = config.setdefault("broker", {})
    env_map = {
        "tdx": {
            "bridge_url": "TDX_BRIDGE_URL",
            "token": "TDX_BRIDGE_TOKEN",
        },
        "futu": {
            "opend_host": "FUTU_OPEND_HOST",
            "opend_port": "FUTU_OPEND_PORT",
        },
        "tiger": {
            "api_key": "TIGER_API_KEY",
            "secret_key": "TIGER_SECRET_KEY",
        },
        "ibkr": {
            "host": "IBKR_HOST",
            "port": "IBKR_PORT",
            "client_id": "IBKR_CLIENT_ID",
        },
    }
    for broker_name, mapping in env_map.items():
        broker_cfg = brokers.setdefault(broker_name, {"enabled": False})
        for field, env_name in mapping.items():
            value = os.getenv(env_name)
            if value:
                broker_cfg[field] = value


def get_data_root(config: dict) -> Path:
    """Resolve absolute data root dir."""
    root = config.get("data", {}).get("root_dir", "./data")
    path = Path(root)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_ui_dir(config: dict) -> Path:
    ui_dir = config.get("server", {}).get("ui_dir", "./nof0")
    path = Path(ui_dir)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_server_config(config: dict) -> dict:
    return config.get("server", {})


def get_market_config(config: dict, market: str) -> dict:
    return config.get("markets", {}).get(market, {})


def get_enabled_markets(config: dict) -> list[str]:
    return [
        name for name, cfg in config.get("markets", {}).items()
        if cfg.get("enabled", True)
    ]


def get_broker_config(config: dict) -> dict:
    return config.get("broker", {})
