#!/usr/bin/env python3
"""
接收期权异动推送

功能：实时接收期权异动推送通知。当已设置的期权异动提醒被触发时，服务端会主动推送异动消息到客户端。
用法：python push_option_event.py --duration 300

前置条件：
- 需先通过 set_option_event_alert 设置提醒条件，推送才会触发
- 回调函数运行在独立子线程中，注意线程安全
"""
import argparse
import json
import time
import sys
import os as _os
sys.path.insert(0, _os.path.normpath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")))
from common import (
    create_quote_context,
    safe_close,
    RET_OK,
)

from futu import OptionEventHandlerBase, RET_ERROR


class OptionEventHandler(OptionEventHandlerBase):
    """期权异动推送回调处理类"""
    def __init__(self, output_json=False):
        super().__init__()
        self.output_json = output_json

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            if self.output_json:
                print(json.dumps({"error": str(content)}, ensure_ascii=False), flush=True)
            else:
                print(f"推送错误: {content}", flush=True)
            return RET_ERROR, content

        if self.output_json:
            print(json.dumps({
                "type": "OPTION_EVENT",
                "data": {
                    "owner_code": content.get("owner_code", ""),
                    "option_code": content.get("option_code", ""),
                    "message": content.get("message", ""),
                }
            }, ensure_ascii=False), flush=True)
        else:
            print(f"\n[期权异动推送] {time.strftime('%H:%M:%S')}")
            print(f"  标的: {content.get('owner_code', '')}")
            print(f"  期权: {content.get('option_code', '')}")
            print(f"  消息: {content.get('message', '')}")

        return RET_OK, content


def push_option_event(duration=300, output_json=False):
    ctx = None
    try:
        ctx = create_quote_context()
        handler = OptionEventHandler(output_json=output_json)
        ctx.set_handler(handler)

        if not output_json:
            print("已注册期权异动推送处理器")
            print(f"等待推送 {duration} 秒（需先通过 set_option_event_alert 设置提醒条件）...")

        time.sleep(duration)

    except KeyboardInterrupt:
        if not output_json:
            print("\n已停止接收推送")
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(f"错误: {e}")
        sys.exit(1)
    finally:
        safe_close(ctx)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="接收期权异动推送")
    parser.add_argument("--duration", type=int, default=300, help="持续接收时间（秒，默认: 300）")
    parser.add_argument("--json", action="store_true", dest="output_json", help="输出 JSON 格式")
    args = parser.parse_args()
    push_option_event(args.duration, args.output_json)
