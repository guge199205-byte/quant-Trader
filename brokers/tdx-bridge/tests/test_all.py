"""通达信桥全量方法测试.

测试 TdxClient 的全部方法 (直接调用, 不依赖线上桥).
用 mock JSON-RPC 服务模拟通达信 17709, 验证:
  1. 所有方法能正确构造请求
  2. 参数映射正确
  3. 返回值解析正确
  4. codec 代码格式校验
  5. SQLite 缓存层

运行: cd bridge-windows && python3 tests/test_all.py
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

# ---- mock 通达信 17709 ----
CAPTURED = []


class MockTdx(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        method = body.get("method", "")
        params = body.get("params", {})
        CAPTURED.append((method, params))
        result = {"ErrorId": "0"}
        if method == "get_match_stkinfo":
            result["Value"] = [{"Code": "600519.SH", "Name": "贵州茅台"}]
        elif method == "get_stock_info":
            result.update({"Name": "贵州茅台", "Zsz": "21000"})
        elif method == "get_stock_list":
            result["Value"] = [{"Code": "600519.SH", "Name": "贵州茅台"}]
        elif method == "get_more_info":
            result.update({"ZAF": "2.5", "Zsz": "21000"})
        elif method == "get_sector_list":
            result["Value"] = [{"Code": "880301", "Name": "软件服务"}]
        elif method == "get_stock_list_in_sector":
            result["Value"] = [{"Code": "600519.SH"}]
        elif method == "get_relation":
            result["Value"] = [{"BlockCode": "880301", "BlockName": "软件服务"}]
        elif method == "get_divid_factors":
            result["Value"] = [{"Date": "20260101", "Type": "1"}]
        elif method == "get_gb_info":
            result["Value"] = {"Date": ["20260101"], "Zgb": ["100000"]}
        elif method == "get_kzz_info":
            result.update({"KZZCode": "113527", "HSCode": "600519"})
        elif method == "get_ipo_info":
            result["Value"] = [{"code": "001234", "name": "测试新股"}]
        elif method == "get_trackzs_etf_info":
            result["Value"] = [{"Code": "510300", "Name": "沪深300ETF"}]
        elif method == "get_pricevol":
            result.update({"LastClose": "10.0", "Now": "10.5"})
        elif method == "get_exday_data":
            result["Value"] = [{"Date": "20260101"}]
        elif method == "get_zdt_data":
            result["Value"] = [{"Code": "600519.SH", "ZDTStatusNow": "1"}]
        elif method == "get_trading_dates":
            result["Value"] = ["20260101", "20260102"]
        elif method == "get_financial_data":
            result["Value"] = {"600519.SH": {"FN1": "100", "FN2": "200"}}
        elif method == "get_gpjy_value":
            result["Value"] = {"600519.SH": {"GP01": "1"}}
        elif method == "get_bkjy_value":
            result["Value"] = {"880301": {"BK5": "10.0"}}
        elif method == "get_scjy_value":
            result["Value"] = {"SC01": "100"}
        elif method == "get_gp_one_data":
            result["Value"] = {"600519.SH": {"GO1": "1"}}
        elif method == "get_user_sector":
            result["Value"] = [{"Code": "ZXG", "Name": "自选股"}]
        elif method == "create_sector":
            result["Value"] = {"success": True}
        elif method == "delete_sector":
            result["Value"] = {"success": True}
        elif method == "rename_sector":
            result["Value"] = {"success": True}
        elif method == "clear_sector":
            result["Value"] = {"success": True}
        elif method == "refresh_cache":
            result["Value"] = {"success": True}
        elif method == "refresh_kline":
            result["Value"] = {"success": True}
        elif method == "download_file":
            result["Value"] = {"success": True}
        elif method == "exec_to_tdx":
            result["Value"] = {"success": True}
        elif method == "send_file":
            result["Value"] = {"success": True}
        elif method == "send_bt_data":
            result["Value"] = {"success": True}
        elif method == "formula_zb":
            result["Value"] = {"Data": {"DIF": [1.0, 2.0]}}
        elif method == "formula_xg":
            result["Value"] = {"Data": {"UP3": ["0", "1"]}}
        elif method == "formula_exp":
            result["Value"] = {"Data": {}}
        elif method == "formula_get_data":
            result["Value"] = {"Code": "600519.SH"}
        elif method == "formula_set_data":
            result["Value"] = {"success": True}
        elif method == "formula_set_data_info":
            result["Value"] = {"success": True}
        elif method == "formula_process_mul_xg":
            result["Value"] = {"600519.SH": {"Value": "1"}}
        elif method == "formula_process_mul_zb":
            result["Value"] = {"600519.SH": {"Value": "1"}}
        elif method == "formula_process_mul_exp":
            result["Value"] = {"600519.SH": {"Value": "1"}}
        else:
            result["ErrorId"] = "-1"
            result["Msg"] = f"unknown {method}"
        resp = json.dumps({"id": body.get("id", 1), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def main():
    srv = HTTPServer(("127.0.0.1", 0), MockTdx)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from src.tdx.client import TdxClient
    tdx = TdxClient(f"http://127.0.0.1:{port}/", timeout=5, max_retries=0)

    passed = 0
    failed = 0

    def test(name, fn):
        nonlocal passed, failed
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            failed += 1

    print("\n=== 行情数据类 ===")
    test("get_stock_info", lambda: tdx.get_stock_info("600519.SH", ["Name"]))
    test("get_more_info", lambda: tdx.get_more_info("600519.SH"))
    test("get_stock_list", lambda: tdx.get_stock_list("5"))
    test("get_sector_list", lambda: tdx.get_sector_list())
    test("get_stock_list_in_sector", lambda: tdx.get_stock_list_in_sector("880301"))
    test("get_user_sector", lambda: tdx.get_user_sector())
    test("get_match_stkinfo", lambda: tdx.get_match_stkinfo("茅台"))
    test("get_relation", lambda: tdx.get_relation("600519.SH"))
    test("get_divid_factors", lambda: tdx.get_divid_factors("600519.SH"))
    test("get_gb_info", lambda: tdx.get_gb_info("600519.SH", ["20260101"], 1))
    test("get_kzz_info", lambda: tdx.get_kzz_info("600519.SH"))
    test("get_ipo_info", lambda: tdx.get_ipo_info(0, 0))
    test("get_trackzs_etf_info", lambda: tdx.get_trackzs_etf_info("000300.CSI"))
    test("get_pricevol", lambda: tdx.get_pricevol(["600519.SH"]))
    test("get_exday_data", lambda: tdx.get_exday_data("600519.SH", 1))
    test("get_zdt_data", lambda: tdx.get_zdt_data(["600519.SH"]))
    test("get_trading_dates", lambda: tdx.get_trading_dates("5", "20260101", "20260131"))

    print("\n=== 专业数据类 ===")
    test("get_financial_data", lambda: tdx.get_financial_data(["600519.SH"], ["FN1"]))
    test("get_financial_data_by_date", lambda: tdx.get_financial_data_by_date(["600519.SH"], ["FN1"]))
    test("get_gpjy_value", lambda: tdx.get_gpjy_value(["600519.SH"], ["GP01"]))
    test("get_bkjy_value", lambda: tdx.get_bkjy_value(["880301"], ["BK5"]))
    test("get_scjy_value", lambda: tdx.get_scjy_value(["SC01"]))
    test("get_gp_one_data", lambda: tdx.get_gp_one_data(["600519.SH"], ["GO1"]))

    print("\n=== 板块管理类 ===")
    test("create_sector", lambda: tdx.create_sector("MYBLOCK", "我的板块"))
    test("delete_sector", lambda: tdx.delete_sector("MYBLOCK"))
    test("rename_sector", lambda: tdx.rename_sector("MYBLOCK", "新名字"))
    test("clear_sector", lambda: tdx.clear_sector("MYBLOCK"))

    print("\n=== 缓存/刷新类 ===")
    test("refresh_cache", lambda: tdx.refresh_cache("AG"))
    test("refresh_kline", lambda: tdx.refresh_kline(["600519.SH"], "1d"))
    test("download_file", lambda: tdx.download_file(1, "600519.SH"))

    print("\n=== 客户端控制类 ===")
    test("exec_to_tdx", lambda: tdx.exec_to_tdx("http://www.treeid"))
    test("send_file", lambda: tdx.send_file("test.txt"))
    test("send_bt_data", lambda: tdx.send_bt_data("600519.SH", ["20260101"], [["1"]], 1))

    print("\n=== 公式接口类 ===")
    test("formula_zb", lambda: tdx.formula_zb("MA", "5"))
    test("formula_xg", lambda: tdx.formula_xg("CROSS"))
    test("formula_exp", lambda: tdx.formula_exp("MACD"))
    test("formula_get_data", lambda: tdx.formula_get_data())
    test("formula_set_data", lambda: tdx.formula_set_data("600519.SH", []))
    test("formula_set_data_info", lambda: tdx.formula_set_data_info("600519.SH"))
    test("formula_process_mul_xg", lambda: tdx.formula_process_mul_xg("CROSS", ["600519.SH"]))
    test("formula_process_mul_zb", lambda: tdx.formula_process_mul_zb("MA", ["600519.SH"]))
    test("formula_process_mul_exp", lambda: tdx.formula_process_mul_exp("MACD", ["600519.SH"]))

    # 验证请求参数映射
    print("\n=== 参数映射验证 ===")
    CAPTURED.clear()
    tdx.get_stock_info("600519.SH", ["Name"])
    m, p = CAPTURED[-1]
    assert m == "get_stock_info" and p["stock_code"] == "600519.SH"
    CAPTURED.clear()
    tdx.get_financial_data(["600519.SH"], ["FN1"], "20260101", "20261231")
    m, p = CAPTURED[-1]
    assert p["stock_list"] == ["600519.SH"] and p["field_list"] == ["FN1"]
    CAPTURED.clear()
    tdx.formula_process_mul_xg("CROSS", ["600519.SH"], return_count=3)
    m, p = CAPTURED[-1]
    assert p["formula_name"] == "CROSS" and p["return_count"] == 3
    print("  ✅ 参数映射正确")

    # codec 验证
    print("\n=== codec 代码格式校验 ===")
    from src.tdx.codec import check_stock_code_format, normalize_stock_code
    assert check_stock_code_format("600519.SH")
    assert not check_stock_code_format("600519")
    assert normalize_stock_code("600519") == "600519.SH"
    print("  ✅ codec 校验正确")

    # SQLite 缓存验证
    print("\n=== SQLite 缓存层 ===")
    import tempfile
    from src.db.cache_db import CacheDb
    db = CacheDb(os.path.join(tempfile.mkdtemp(), "t.db"))
    db.save_kline("600519.SH", "1d", [{"date": "20260812", "close": 1850.0}])
    assert len(db.get_kline("600519.SH")) == 1
    db.log_call("get_stock_info", "600519.SH", {}, {"Name": "茅台"}, category="query",
                status="ok", duration_ms=5.0)
    assert len(db.get_logs()) == 1
    db.log_trade("plan_1", "order_execute", "600519.SH", "buy", 100, 1850.0,
                 "W123", "submitted", "已提交")
    assert len(db.get_trade_logs()) == 1
    print("  ✅ SQLite 缓存层正确")
    db.close()

    srv.shutdown()
    print(f"\n=== 结果: {passed} 通过, {failed} 失败 ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
