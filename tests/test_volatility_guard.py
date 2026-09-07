"""波动触发防护单测：坏 tick 丢弃 / 好 tick 保留 / 同向冷却 / 反向不受限。

运行：/home/zbox/baymax/.venv/bin/python -m pytest tests/test_volatility_guard.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_hourly_analysis as L  # noqa: E402

ROWS = [{"code": "688183.SH", "name": "生益电子", "price": 118.3,
         "pnl_pct": -11.5, "day_chg": -5.13}]


class FakeBroker:
    def get_klines(self, *a, **k):
        return []


def setup_module(module):
    module._orig_state = L.STATE_PATH
    module._orig_rows, module._orig_qf = L.build_rows, L.quote_fallback
    L.STATE_PATH = Path("/tmp/test_state_guard.json")
    L.build_rows = lambda broker, positions, names: [dict(r) for r in ROWS]
    L.MIN_ANALYSIS_INTERVAL_MIN = 0


def teardown_module(module):
    L.STATE_PATH = module._orig_state
    L.build_rows, L.quote_fallback = module._orig_rows, module._orig_qf
    Path("/tmp/test_state_guard.json").unlink(missing_ok=True)


def _set_state(d):
    Path("/tmp/test_state_guard.json").write_text(json.dumps(d), encoding="utf-8")


def _base():
    _set_state({"last_pnl": {"688183.SH": -5.0}, "last_day": {}, "last_ts": ""})


def test_bad_tick_dropped(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 131.5)  # 偏差 11%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_good_tick_kept(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)  # 偏差 0.17%
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])


def test_same_direction_cooldown(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)
    _base()
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    # 60 分钟内同向再触发 → 冷却丢弃
    assert L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}]) is None


def test_reverse_direction_not_cooled(monkeypatch):
    monkeypatch.setattr(L, "quote_fallback", lambda code: 118.1)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    _set_state({"last_pnl": {"688183.SH": -20.0}, "last_day": {}, "last_ts": "",
                "last_trigger": {"688183.SH": {"dir": "down", "ts": now.isoformat()}}})
    r = L.check_volatility(FakeBroker(), [{"stock_code": "688183.SH"}])
    assert r and "盈亏" in r  # 反向（盈亏转好）不受冷却限制，pnl 触发保留
