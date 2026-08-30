"""端到端集成测试: 用独立线程的 mock TDX 服务验证 HTTP 桥、执行器、文件通道、止损守护全链路.

运行: cd ~/trading-bridge/bridge-windows && python3 tests/test_e2e.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "a" * 64


# ---- mock TDX (独立线程, 标准库) ----

class MockTdx:
    def __init__(self, price=11.00, sell_value="1"):
        self.price = price
        self.sell_value = sell_value
        self.orders_placed = []
        self.port = None
        self._httpd = None
        self._thread = None

    def start(self):
        self._httpd = HTTPServer(("127.0.0.1", 0), self._handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler(self, *a, **kw):
        handler = _MockHandler
        handler.mock = self
        return handler(*a, **kw)


class _MockHandler(BaseHTTPRequestHandler):
    mock = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        method = body.get("method", "")
        result = {"ErrorId": "0"}

        if method == "get_match_stkinfo":
            result["Value"] = [{"Code": "600519.SH", "Name": "贵州茅台"}]
        elif method == "stock_account":
            result["Value"] = 0
        elif method == "query_stock_asset":
            result.update({"Currency": "人民币", "Balance": "100000.00", "Cash": "80000.00",
                           "Asset": "200000.00", "MarketValue": "120000.00"})
        elif method == "query_stock_positions":
            result["Value"] = [{"Code": "000001.SZ", "Cbj": "10.50", "TotalVol": "1000",
                                "CanUseVol": "1000"},
                               {"Code": "600519.SH", "Cbj": "10.00", "TotalVol": "100",
                                "CanUseVol": "100"}]
        elif method == "query_stock_orders":
            result["Value"] = []
        elif method == "order_stock":
            params = body.get("params", {})
            side = "sell" if params.get("order_type") == 1 else "buy"
            self.mock.orders_placed.append({"stock": params.get("stock_code"), "side": side,
                                            "vol": params.get("order_volume")})
            result.update({"Value": self.mock.sell_value, "Wtbh": f"W{int(time.time())}",
                           "Msg": "已发送信号至客户端，待用户确认！"})
        elif method == "cancel_order_stock":
            result.update({"Value": "1", "Msg": "撤单成功"})
        elif method == "get_market_snapshot":
            result.update({"Now": f"{self.mock.price}", "Max": "12.00", "Min": "10.00"})
        elif method == "get_market_data":
            result.update({"Close": [f"{self.mock.price}"], "Date": ["20260812"]})
        else:
            result["ErrorId"] = "-1"
            result["Msg"] = f"unknown {method}"

        resp = json.dumps({"id": body.get("id", 1), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


async def run_tests():
    from aiohttp import web, ClientSession
    from src.tdx.client import TdxClient
    from src.executor.plan_executor import PlanExecutor
    from src.executor.stop_loss_daemon import StopLossDaemon
    from src.channel.http_server import start_http_server
    from src.channel.file_sync import FileSyncChannel
    from src.core.trade_plan import TradePlan, Order

    tmp = tempfile.mkdtemp()
    shared = os.path.join(tmp, "shared")

    # 1. mock TDX
    mock = MockTdx(price=11.00).start()
    tdx = TdxClient(f"http://127.0.0.1:{mock.port}/", timeout=5, max_retries=0)
    assert tdx.health_check(), "mock TDX 健康检查失败"
    print("[PASS] TDX 客户端连接 mock 服务成功")

    executor = PlanExecutor(tdx, shared)
    from src.executor.order_tracker import OrderTracker
    tracker = OrderTracker(tdx, os.path.join(tmp, "active_orders.json"))

    # 2. HTTP 桥 (固定端口, 测试专用)
    HTTP_PORT = 18550
    runner = await start_http_server("127.0.0.1", HTTP_PORT, TOKEN, tdx, executor, tracker, None)
    base = f"http://127.0.0.1:{HTTP_PORT}"

    async with ClientSession() as session:
        async def api(path, body, token=TOKEN):
            async with session.post(base + path, json=body,
                                    headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status == 200:
                    return resp.status, await resp.json()
                try:
                    return resp.status, await resp.json()
                except Exception:
                    return resp.status, {"error": await resp.text()}

        # 3. execute_plan (卖先买后)
        plan = TradePlan(
            plan_id="plan_e2e_001",
            orders=[Order(stock_code="600519.SH", side="buy", volume=100, price=1650.0),
                    Order(stock_code="000001.SZ", side="sell", volume=500)])
        report = executor.execute_plan(plan)
        assert report["status"] == "executed", f"执行失败: {report}"
        assert os.path.exists(os.path.join(shared, "trade_log.jsonl")), "交易记录未同步"
        print("[PASS] execute_plan 成功 + 交易记录已同步")

        # 卖先买后: 第一个下单应是卖出
        assert mock.orders_placed[0]["side"] == "sell", "卖出应先于买入"
        print("[PASS] 卖先买后顺序正确:", [o["side"] for o in mock.orders_placed])

        # 4. 幂等
        dup = executor.execute_plan(plan)
        assert dup["status"] == "duplicate"
        print("[PASS] 幂等去重生效")

        # 5. HTTP 鉴权 + 查账号
        status, acct = await api("/api/v1/account/query", {"account_type": "stock"})
        assert status == 200 and acct["asset"]["cash"] == 80000.0, f"{status} {acct}"
        print(f"[PASS] HTTP 查询账号: {acct['asset']}")

        status, err = await api("/api/v1/account/query", {}, token="wrong")
        assert status == 401
        print("[PASS] 错误 token 返回 401")

        status, healthy = await api("/api/v1/health", {}, token="x")
        assert status == 405  # health 是 GET, POST 返回 405 (路由已生效)
        print("[PASS] 健康检查路由已挂载 (POST 正确返回 405)")

        async with session.get(base + "/api/v1/health") as resp:
            h = await resp.json()
            assert h["status"] == "ok"
        print("[PASS] 健康检查 GET OK")

        # 6. HTTP 下单 (新 plan_id 避免幂等冲突)
        http_plan = TradePlan(plan_id="plan_http_001",
                              orders=[Order(stock_code="600519.SH", side="buy", volume=100, price=1650.0)])
        status, placed = await api("/api/v1/plans/execute", http_plan.to_dict())
        assert status == 200 and placed["status"] == "executed", f"{status} {placed}"
        print(f"[PASS] HTTP 桥下单: {placed['status']}")

        # 7. 止损守护 (高价不触发)
        sltp = StopLossDaemon(tdx, os.path.join(tmp, "stop_loss_state.json"), poll_interval=0.1)
        sltp.register_items([{"stock_code": "600519.SH", "entry_price": 10.0, "volume": 100,
                              "stop_loss_pct": 0.05}])
        task = asyncio.create_task(sltp.run())
        await asyncio.sleep(0.2)
        assert sltp.state_items()[0]["enabled"], "高价不应触发止损"
        print("[PASS] 止损未误触发 (现价 11.0 > 止损线 9.5)")

        # 切换 mock 为低价触发
        mock.price = 9.40
        mock.sell_value = "2"
        await asyncio.sleep(0.2)
        assert not sltp.state_items()[0]["enabled"], "止损未触发"
        assert any(o["side"] == "sell" and o["stock"] == "600519.SH" for o in mock.orders_placed), "未提交卖出"
        print("[PASS] 止损触发, 已自动提交卖出")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # 8. 文件通道 (Windows 侧消费 pending)
    executor2 = PlanExecutor(tdx, shared)
    fs = FileSyncChannel(shared, executor2, poll_interval=0.05)
    fs_task = asyncio.create_task(fs.run())
    plan2 = TradePlan(plan_id="plan_fs_001",
                      orders=[Order(stock_code="000001.SZ", side="buy", volume=200)])
    # Windows 侧没有 write_plan, 模拟 Linux 写入 pending 文件
    os.makedirs(os.path.join(shared, "trade_plans", "pending"), exist_ok=True)
    with open(os.path.join(shared, "trade_plans", "pending", "plan_test.json"),
              "w", encoding="utf-8") as f:
        json.dump(plan2.to_dict(), f, ensure_ascii=False, indent=2)
    await asyncio.sleep(0.3)
    assert not fs._list_pending(), "文件通道未处理计划"
    reports = [f for f in os.listdir(os.path.join(shared, "execution_reports"))
               if f.endswith(".json")]
    assert reports, "未生成执行报告"
    print("[PASS] 文件通道: pending 计划被消费, 已生成执行报告")
    fs_task.cancel()
    try:
        await fs_task
    except asyncio.CancelledError:
        pass

    # 清理
    await runner.cleanup()
    mock.stop()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== 全部测试通过 ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
