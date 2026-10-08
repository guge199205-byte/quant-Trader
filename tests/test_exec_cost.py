"""执行损耗（TCA）口径的单元测试——**符号约定是这份文件的核心契约**。

背景（2026-09-19）：机构的 TCA 回答一句"下单这件事花了多少钱"，而本仓库此前
只能回答"模型选得对不对"（decision_track 明确写着"不衡量执行滑点"）。要补这一层，
第一件事不是写报告，是**把符号约定钉死**：买卖两侧的"正号"必须都表示"比基准差"，
否则报告会把赚钱的滑点报成亏钱的，且无人能一眼看出。

本文件的每一条断言都在钉这个约定，不是形式主义：
  - 买入：成交价高于基准 = 差（+）；
  - 卖出：成交价低于基准 = 差（+）；
  - 缓冲用尽度：0 = 按基准成交，100 = 成交在限价上（把缓冲吃满），负 = 优于基准；
  - 任何缺项/脏值 → None（fail-open，报告按"不可定价"计数，绝不吐 NaN 进聚合）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import exec_cost  # noqa: E402


# ---------------------------------------------------------------- slip_bps

def test_buy_paying_more_than_reference_is_positive():
    """买入成交价高于基准 = 比基准差 = 正号（不管"价格涨了"是好事还是坏事）。"""
    assert exec_cost.slip_bps("buy", 10.0, 10.1) == 100.0
    assert exec_cost.slip_bps("buy", 10.0, 9.9) == -100.0


def test_sell_selling_lower_than_reference_is_positive():
    """卖出成交价低于基准 = 比基准差 = 正号。**两侧正号同义**（都表示吃亏）。"""
    assert exec_cost.slip_bps("sell", 10.0, 9.9) == 100.0
    assert exec_cost.slip_bps("sell", 10.0, 10.1) == -100.0


def test_slip_bps_is_scale_invariant():
    """同样的相对偏差，价格量级不同（5 元 vs 130 元）bps 必须一致。"""
    a = exec_cost.slip_bps("buy", 5.0, 5.05)
    b = exec_cost.slip_bps("buy", 130.0, 131.3)
    assert abs(a - b) < 1e-9


def test_slip_bps_rejects_bad_input():
    """缺项/零价/非数字/未知方向 → None（不是 0，不是异常）。

    报"0 bps"与报"不可定价"在机构语境里是两件事：前者说"这笔执行完美"，
    后者说"这笔我们不知道"，报告必须能区分。
    """
    assert exec_cost.slip_bps("buy", None, 10.0) is None
    assert exec_cost.slip_bps("buy", 10.0, None) is None
    assert exec_cost.slip_bps("buy", 0, 10.0) is None
    assert exec_cost.slip_bps("buy", 10.0, 0) is None
    assert exec_cost.slip_bps("buy", "abc", 10.0) is None
    assert exec_cost.slip_bps("short", 10.0, 10.0) is None
    assert exec_cost.slip_bps("buy", float("nan"), 10.0) is None


# ---------------------------------------------------------------- cushion_used_bps

def test_cushion_used_is_zero_at_reference_and_100_at_limit():
    """缓冲用尽度：按基准成交 0，成交在限价上 100——买卖两侧同式。"""
    assert exec_cost.cushion_used_bps("buy", 10.0, 10.1, 10.0) == 0.0
    assert exec_cost.cushion_used_bps("buy", 10.0, 10.1, 10.1) == 100.0
    assert exec_cost.cushion_used_bps("sell", 10.0, 9.9, 10.0) == 0.0
    assert exec_cost.cushion_used_bps("sell", 10.0, 9.9, 9.9) == 100.0


def test_cushion_used_can_be_negative_when_fill_beats_reference():
    """优于基准成交 → 负值（买入成交在基准下方 / 卖出成交在基准上方）。"""
    assert exec_cost.cushion_used_bps("buy", 10.0, 10.1, 9.95) == -50.0
    assert exec_cost.cushion_used_bps("sell", 10.0, 9.9, 10.05) == -50.0


def test_cushion_used_is_half_when_fill_is_mid_cushion():
    """真实的 09-17 买入：基准 33.069、限价 33.40、成交 32.99 → 负（低于基准）。"""
    got = exec_cost.cushion_used_bps("buy", 33.069, 33.40, 32.99)
    assert got is not None and got < 0


def test_cushion_used_rejects_no_buffer():
    """零缓冲（限价==基准）不可定义用尽度 → None，不是除零崩。"""
    assert exec_cost.cushion_used_bps("buy", 10.0, 10.0, 10.0) is None
    assert exec_cost.cushion_used_bps("buy", None, 10.1, 10.0) is None


# ---------------------------------------------------------------- tape_fields

def test_tape_fields_carries_the_three_prices_and_two_stamps():
    """三段价（基准/限价/成交）+ 委托量 + 三个时刻，键名在这里定死。"""
    f = exec_cost.tape_fields(side="buy", ref_px=33.069, limit_px=33.40, fill_px=32.99,
                              wanted=300, filled=300, decided_ts="2026-09-17T09:35:01+08:00",
                              submit_ts="2026-09-17T09:41:10+08:00",
                              fill_ts="2026-09-17T09:41:14+08:00", path="llm_trade")
    assert f["ref_px"] == 33.069 and f["limit_px"] == 33.40 and f["fill_px"] == 32.99
    assert f["wanted"] == 300 and f["filled"] == 300
    assert f["decided_ts"].startswith("2026-09-17") and f["submit_ts"] and f["fill_ts"]
    assert f["tca_path"] == "llm_trade"


def test_tape_fields_drops_missing_values_instead_of_writing_nulls():
    """缺值不写 None/NaN 进流水：报告侧按"键不存在"= 不可定价，语义单一。

    写 `ref_px: null` 与不写这个键，在 JSON 里是两种东西；只保留一种。
    """
    f = exec_cost.tape_fields(side="sell", ref_px=10.0, limit_px=None, fill_px=9.9,
                              wanted=100, filled=100, path="llm_trade")
    assert "limit_px" not in f and "ref_px" in f and "fill_px" in f
    f2 = exec_cost.tape_fields(side="sell", ref_px=float("nan"), limit_px=9.9, fill_px=9.9)
    assert "ref_px" not in f2


def test_tape_fields_normalizes_side_and_never_raises():
    """方向大小写/缺省不炸；未知方向直接返回空 dict（宁可少记，不记半条）。"""
    assert exec_cost.tape_fields(side="BUY", ref_px=1.0, limit_px=1.0, fill_px=1.0)["side"] == "buy"
    assert exec_cost.tape_fields(side="", ref_px=1.0, limit_px=1.0, fill_px=1.0) == {}
    assert exec_cost.tape_fields(side="buy", ref_px=object(), limit_px=1.0,
                                 fill_px=1.0).get("ref_px") is None


# ---------------------------------------------------------------- normalize_row

def _fill_row(**kw):
    row = {"ts": "2026-09-17T09:41:14+08:00", "mode": "execute", "agent": "a1",
           "code": "002709.SZ", "side": "buy", "volume": 300, "price": 32.99,
           "remaining": 0, "ref_px": 33.069, "limit_px": 33.40, "wanted": 300,
           "decided_ts": "2026-09-17T09:35:01+08:00", "submit_ts": "2026-09-17T09:41:10+08:00",
           "fill_ts": "2026-09-17T09:41:14+08:00", "tca_path": "llm_trade",
           "fill": {"order_id": "206163", "filled_price": 32.99, "filled_volume": 300}}
    row.update(kw)
    return row


def test_normalize_row_reads_a_full_tape_row():
    r = exec_cost.normalize_row(_fill_row())
    assert r["side"] == "buy" and r["fill_px"] == 32.99 and r["ref_px"] == 33.069
    assert r["limit_px"] == 33.40 and r["wanted"] == 300 and r["filled"] == 300
    assert r["date"] == "2026-09-17" and r["order_id"] == "206163"
    assert r["path"] == "llm_trade"


def test_normalize_row_derives_wanted_from_remaining_when_absent():
    """老行（改造前写的）没有 wanted：由 成交+剩余 推。推不出就留 None，不猜。"""
    row = _fill_row()
    del row["wanted"]
    row["volume"], row["remaining"] = 100, 200
    assert exec_cost.normalize_row(row)["wanted"] == 300
    row2 = _fill_row()
    del row2["wanted"]
    del row2["remaining"]
    assert exec_cost.normalize_row(row2)["wanted"] is None


def test_normalize_row_applies_legacy_limit_from_ack_join():
    """历史行没有 limit_px：由调用方按委托号从日志补齐（缺 limit 的卖单补不上）。"""
    row = _fill_row()
    del row["limit_px"]
    del row["ref_px"]
    r = exec_cost.normalize_row(row, legacy_limit=33.40)
    assert r["limit_px"] == 33.40
    assert r["ref_px"] is None            # 基准不臆造：补得出限价不等于知道基准


def test_normalize_row_rejects_non_fill_rows():
    """委托/错误/挂队行不是成交，不能进滑点样本（进了会把"没成交"算成"成交得好"）。"""
    assert exec_cost.normalize_row({"mode": "fill_abort", "side": "buy", "price": 10.0}) is None
    assert exec_cost.normalize_row({"mode": "execute", "error": "boom"}) is None
    assert exec_cost.normalize_row({"mode": "execute", "agent": "a", "code": "x",
                                    "side": "buy", "volume": 0, "price": 10.0}) is None


def test_normalize_row_rejects_inflight_pending_rows():
    """「余量挂在途」的 trace 行不是成交，不许进样本。

    执行路径在部分成交后另写一行 mode=execute/execute_intraday 的在途 trace
    （带 pending/untracked 键，price 写的是限价/基准价）。把它读成成交 =
    按我们自己报的价假装成交了，且与同委托号的成交行按量加权 → 污染执行价。
    """
    row = _fill_row(volume=200, price=33.40, pending=True, untracked=False)
    del row["fill"]
    assert exec_cost.normalize_row(row) is None
    row2 = _fill_row(volume=200, price=33.40, pending=False, untracked=True)
    del row2["fill"]
    assert exec_cost.normalize_row(row2) is None


def test_normalize_row_maps_legacy_mode_to_path():
    """老行没有 tca_path：按 mode 归一到与接线后相同的路径词表（跨期同组比较）。"""
    row = _fill_row()
    del row["tca_path"]
    assert exec_cost.normalize_row(row)["path"] == "llm_trade"
    row2 = _fill_row(mode="execute_intraday")
    del row2["tca_path"]
    assert exec_cost.normalize_row(row2)["path"] == "hourly"


def test_normalize_row_accepts_reconcile_fill_confirm():
    """对账补记行（fill_confirm）也是成交：限价随 pending 一路带过来。"""
    r = exec_cost.normalize_row({
        "ts": "2026-09-15T10:31:00+08:00", "mode": "fill_confirm", "agent": "a1",
        "code": "600817.SH", "side": "sell", "volume": 600, "price": 9.41,
        "order_id": "111693", "limit_px": 9.50, "wanted": 600, "submit_ts": "2026-09-15T09:36:00+08:00"})
    assert r is not None and r["filled"] == 600 and r["limit_px"] == 9.50
    assert r["order_id"] == "111693"


# ---------------------------------------------------------------- summarize

def _norm(side, ref, fill, filled=100, limit=None, path="llm_trade", code="600000.SH"):
    return exec_cost.normalize_row(_fill_row(
        side=side, ref_px=ref, price=fill, limit_px=limit, volume=filled, wanted=filled,
        remaining=0, code=code, tca_path=path, fill={"order_id": "x", "filled_volume": filled}))


def test_summarize_weights_by_notional_not_by_count():
    """一笔 1 万股的大单 与 一笔 100 股的小单：大单必须主导加权均值。

    这是"钱花在哪"的本义：小单滑点再大，也不该和一个仓位级别的单等权。
    """
    rows = [_norm("buy", 10.0, 10.1, filled=10000),     # +100 bps，名义 10.1 万
            _norm("buy", 10.0, 9.0, filled=100)]        # −1000 bps，名义 900
    s = exec_cost.summarize(rows)
    assert s["n"] == 2
    assert s["slip_bps_w"] > 0                            # 大单方向说话
    assert abs(s["slip_bps_mean"] - (-450.0)) < 1e-6      # 简单均值仍是两者的中点


def test_summarize_reports_n_only_for_priced_samples():
    """没有基准价的行（历史卖单）不进滑点样本，但要在覆盖里能被看见。"""
    rows = [_norm("buy", 10.0, 10.1),
            exec_cost.normalize_row(_fill_row(side="sell", code="000001.SZ",
                                              ref_px=None, limit_px=None))]
    rows[1]["ref_px"] = None   # 老行确实没有基准
    s = exec_cost.summarize([r for r in rows if r])
    assert s["n"] == 1 and s["n_unpriced"] == 1
    assert s["slip_bps_w"] == 100.0


def test_summarize_median_and_percentiles_are_robust_to_one_outlier():
    """中位数/p10/p90 用来说明分布：一笔极端值不许把中位拽走。"""
    rows = [_norm("buy", 10.0, 10.0),
            _norm("buy", 10.0, 10.0),
            _norm("buy", 10.0, 10.0),
            _norm("buy", 10.0, 12.0)]     # +2000 bps 一笔
    s = exec_cost.summarize(rows)
    assert s["slip_bps_med"] == 0.0
    assert s["slip_bps_p90"] > 0


def test_summarize_empty_is_all_none_not_zero():
    """空样本 = 全部 None（报告据此印"无样本"），不许印 0.00 bps。"""
    s = exec_cost.summarize([])
    assert s["n"] == 0 and s["slip_bps_w"] is None and s["slip_bps_med"] is None


def test_summarize_counts_fill_rate_denominator():
    """成交率：成交笔数 / (成交笔数 + 零成交终态笔数)，口径写在这一个函数里。"""
    rows = [_norm("buy", 10.0, 10.0), _norm("buy", 10.0, 10.0)]
    s = exec_cost.summarize(rows, zero_fill_orders=2)
    assert s["n_orders"] == 4 and abs(s["fill_rate"] - 0.5) < 1e-9


def test_summarize_latency_from_round_level_decided_ts():
    """决策→提交延迟：决策时刻是轮级的（同轮各单共用），报告必须说清粒度。"""
    rows = [_norm("buy", 10.0, 10.0)]
    rows[0]["decided_ts"] = "2026-09-17T09:35:01+08:00"
    rows[0]["submit_ts"] = "2026-09-17T09:41:10+08:00"
    s = exec_cost.summarize(rows)
    assert abs(s["decide_to_submit_min_med"] - 6.15) < 0.01


def test_summarize_groups_by_path_and_side():
    """按路径/方向分组：两种路径的成交机制不同（09:35 有 wait_fill 长轮询，
    整点轮短轮询），混算会把差异洗掉。"""
    rows = [_norm("buy", 10.0, 10.1, path="llm_trade"),
            _norm("sell", 10.0, 9.0, path="hourly"),
            _norm("sell", 10.0, 9.0, path="hourly")]
    g = exec_cost.summarize(rows)["groups"]
    assert set(g) == {"llm_trade/buy", "hourly/sell"}
    assert g["hourly/sell"]["n"] == 2
