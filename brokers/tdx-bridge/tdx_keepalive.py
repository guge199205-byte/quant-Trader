"""
通达信 TQ 常驻服务 - 保持 17709 HTTP 服务持续监听

在通达信 TQ 策略管理界面"运行"本策略后，进程不退出，
tqcenter 会话持续保持，17709 JSON-RPC 服务一直可用，
供 Windows 桥 (bridge-windows) 和 QuantMind 远程调用。

使用方法:
  1. 通达信 TQ 策略管理器 → 新建策略 → 粘贴本代码
  2. 点"运行"，保持策略运行状态（不要停止）
  3. 确认 17709 监听: curl http://127.0.0.1:17709/
"""

import time
from tqcenter import tq

def main():
    # 初始化 TQ 会话 (拉起 17709 HTTP 服务)
    tq.initialize(__file__)
    print("TQ 常驻服务已启动, 17709 监听中...")
    print("请保持本策略运行, 关闭即停止 17709 服务")
    print("按 Ctrl+C 或停止策略退出")

    try:
        # 心跳: 定期保持会话活跃
        tick = 0
        while True:
            time.sleep(30)
            tick += 1
            if tick % 10 == 0:  # 每 5 分钟打印一次心跳
                print(f"TQ 常驻心跳 #{tick}")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"TQ 常驻异常: {e}")
    finally:
        try:
            tq.close()
        except Exception:
            pass
        print("TQ 常驻服务已停止")

if __name__ == "__main__":
    main()
