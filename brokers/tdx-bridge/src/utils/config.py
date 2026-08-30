import os

import yaml

# 内置默认配置 (exe 打包后 config.yaml 不存在时使用)
DEFAULT_CONFIG = """
bridge:
  name: "bridge-windows"
  log_level: "INFO"
cache:
  enabled: true
security:
  rate_limit_per_minute: 60
  write_rate_limit: 10
  fail_ban_threshold: 5
  fail_ban_seconds: 30
ui:
  auto_open: true
auth:
  token: "${BRIDGE_AUTH_TOKEN}"
  extra_tokens: ""
channels:
  mode: "auto"
  http:
    enabled: true
    host: "0.0.0.0"
    port: 8550
    timeout_seconds: 30
  file_sync:
    enabled: true
    shared_dir: "${SHARED_DIR}"
failover:
  primary_channel: "http"
  fallback_channel: "file_sync"
  http_max_retries: 3
  http_retry_delay_ms: 1000
  health_check_interval_seconds: 10
  auto_recover_http: true
tdx:
  base_url: "http://127.0.0.1:17709/"
  request_timeout_seconds: 15
  max_retries: 2
sltp_daemon:
  enabled: true
  poll_interval_seconds: 5
order_tracking:
  state_file: "./data/active_orders.json"
  sync_interval_seconds: 15
"""


class Config:
    def __init__(self, path: str):
        # 优先读外部 config.yaml; 找不到用内置默认
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.raw = yaml.safe_load(f) or {}
        else:
            self.raw = yaml.safe_load(DEFAULT_CONFIG) or {}

    def _resolve(self, value):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            name = value[2:-1]
            return os.environ.get(name, "")
        return value

    def get(self, dotted: str, default=None):
        cur = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return default
            cur = cur.get(part)
            if cur is None:
                return default
        return self._resolve(cur)

    def token(self) -> str:
        tok = self.get("auth.token", "")
        if not tok:
            raise ValueError("auth.token 未配置(通过 BRIDGE_AUTH_TOKEN 环境变量注入)")
        return tok
