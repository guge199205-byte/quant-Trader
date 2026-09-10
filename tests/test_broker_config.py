"""券商配置服务单测（backend/services/tdx_live.py 的 qmt 分支）。

背景：QMT 的配置落在自有 config/qmt_bridge.json（QmtBridgeBroker / scripts/qmt_probe.py
同源读取），设置页卡片经 /api/broker-config/qmt 直接编辑它——**不能在 brokers.json 再存
一份**，否则必然漂移。这里钉住四件事：
  1. 读写走 QMT 自有文件，且不动 brokers.json；
  2. 文件里的其它键（_comment/timeout/strategy_name）不被覆盖写丢；
  3. redis_port/db 以整数写回（不是 "6379" 字符串）；
  4. 测试连接分支成功/失败的消息形状（前端卡片直接显示 message）。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_broker_config.py -q
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import tdx_live as T  # noqa: E402

QMT_FILE_SEED = {
    "account_id": "40327478",
    "account_type": "STOCK",
    "redis_host": "192.168.31.68",
    "redis_port": 6379,
    "redis_db": 5,
    "redis_password": "",
    "timeout": 10.0,
    "strategy_name": "baymax",
    "_comment": "QMT 桥：与 quantmind 共用本机 Redis(6379/db5)",
}


@pytest.fixture()
def cfg_files(tmp_path, monkeypatch):
    """两个配置文件都重定向到 tmp_path（测试不碰真实 config/）。"""
    qmt = tmp_path / "qmt_bridge.json"
    brokers = tmp_path / "brokers.json"
    monkeypatch.setattr(T, "QMT_FILE", qmt)
    monkeypatch.setattr(T, "BROKERS_FILE", brokers)
    qmt.write_text(json.dumps(QMT_FILE_SEED, ensure_ascii=False), encoding="utf-8")
    return qmt, brokers


def test_qmt_get_reads_own_file_and_masks_password(cfg_files):
    qmt, _ = cfg_files
    qmt.write_text(json.dumps({**QMT_FILE_SEED, "redis_password": "s3cret"},
                              ensure_ascii=False), encoding="utf-8")

    out = T.get_broker_config("qmt")

    assert out["success"] is True
    assert out["label"] == "迅投 QMT"
    assert out["fields"]["account_id"] == "40327478"
    assert out["fields"]["redis_password_configured"] is True
    assert "redis_password" not in out["fields"]      # 敏感字段只写不回显


def test_qmt_update_writes_own_file_and_keeps_unknown_keys(cfg_files):
    qmt, brokers = cfg_files

    T.update_broker_config("qmt", {"account_id": "999", "redis_port": "6380"})

    saved = json.loads(qmt.read_text(encoding="utf-8"))
    assert saved["account_id"] == "999"
    assert saved["redis_port"] == 6380
    assert isinstance(saved["redis_port"], int)       # 数值字段不写成字符串
    assert saved["timeout"] == 10.0                   # 未提交的键原样保留
    assert saved["_comment"].startswith("QMT 桥")
    assert not brokers.exists()                       # 不碰 brokers.json


def test_qmt_update_rejects_bad_port_and_unknown_field(cfg_files):
    with pytest.raises(ValueError, match="需为整数"):
        T.update_broker_config("qmt", {"redis_db": "五号库"})
    with pytest.raises(ValueError, match="无效字段"):
        T.update_broker_config("qmt", {"nope": "x"})


def test_other_brokers_still_use_brokers_json(cfg_files):
    qmt, brokers = cfg_files

    T.update_broker_config("tiger", {"tiger_id": "abc"})

    saved = json.loads(brokers.read_text(encoding="utf-8"))
    assert saved["tiger"]["tiger_id"] == "abc"
    assert json.loads(qmt.read_text(encoding="utf-8"))["account_id"] == "40327478"


def test_unknown_broker_raises(cfg_files):
    with pytest.raises(ValueError, match="未知券商"):
        T.get_broker_config("nope")


def test_qmt_test_connection_success_and_failure(cfg_files, monkeypatch):
    import agent_tools.brokers.qmt_bridge as QB

    class _FakeOk:
        def _account_query(self):
            return {"asset": {"asset": 23850348.0, "cash": 21506660.0},
                    "positions": [{"stock_code": "600000.SH"}]}

    monkeypatch.setattr(QB, "QmtBridgeBroker", _FakeOk)
    ok = asyncio.run(T.test_broker_connection("qmt"))
    assert ok["success"] is True
    assert "¥23,850,348" in ok["message"] and "1 只" in ok["message"]

    class _FakeBoom:
        def __init__(self):
            raise RuntimeError("redis rpc timeout: ping")

    monkeypatch.setattr(QB, "QmtBridgeBroker", _FakeBoom)
    bad = asyncio.run(T.test_broker_connection("qmt"))
    assert bad["success"] is False
    assert "redis rpc timeout" in bad["message"]
