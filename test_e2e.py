#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E 端到端自动化集成测试
验证纯 Python Web UI (learnus_ui.py) 与核心引擎 (learnus.py) 之间的全链路通信:
1. 启动 Web 控制台服务器 (后台线程)
2. HTTP GET 检查 UI 首页 HTML 渲染
3. HTTP POST /api/run 触发测试任务 (todo)
4. 连接 /api/logs (SSE 流) 接收实时任务日志输出
5. 验证执行退出码与状态流转
"""

import json
import socketserver
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import learnus_ui

def run_e2e_test():
    test_port = 8999
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("", test_port), learnus_ui.LearnUsWebHandler)

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"[1/5] ✅ 测试服务器已成功在端口 {test_port} 启动")

    time.sleep(0.5)
    base_url = f"http://127.0.0.1:{test_port}"

    # 1. 验证 HTML 页面访问
    with urllib.request.urlopen(f"{base_url}/") as res:
        assert res.status == 200, f"HTTP status expected 200, got {res.status}"
        html = res.read().decode("utf-8")
        assert "LearnUs 智能知识库助手" in html
        assert "纯 Python 原生版" in html
        print("[2/5] ✅ UI 首页渲染测试通过 (状态码 200，页面元素完整)")

    # 2. 验证异步 SSE 日志流建立
    logs_received = []
    stop_stream = threading.Event()

    def sse_listener():
        req = urllib.request.Request(f"{base_url}/api/logs")
        with urllib.request.urlopen(req) as stream:
            for line in stream:
                if stop_stream.is_set():
                    break
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data:"):
                    payload = json.loads(line_str[5:].strip())
                    logs_received.append(payload)

    sse_thread = threading.Thread(target=sse_listener, daemon=True)
    sse_thread.start()
    time.sleep(0.5)
    assert len(logs_received) > 0, "未收到初始状态 SSE 消息"
    print(f"[3/5] ✅ SSE 实时流通信握手成功 (已捕获初始握手数据: {logs_received[0]})")

    # 3. 触发真实任务: 查看待办 (todo)
    req = urllib.request.Request(
        f"{base_url}/api/run",
        data=json.dumps({"action": "todo", "course": "채플"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as res:
        resp = json.loads(res.read().decode("utf-8"))
        assert resp["ok"] is True, f"任务启动失败: {resp}"
    print("[4/5] ✅ 任务触发接口 (/api/run) 请求成功，后台任务平稳启动")

    # 4. 等待任务完成并收集实时终端输出
    print("      ⏳ 正在等待纯 Python 核心引擎返回执行结果...")
    max_wait = 30
    start_t = time.time()
    task_finished = False

    while time.time() - start_t < max_wait:
        time.sleep(0.5)
        # 检查是否捕获到了包含完成或退出的日志
        text_stream = "".join(x.get("content", "") for x in logs_received if x.get("type") == "log")
        if "任务完成" in text_stream:
            task_finished = True
            break

    assert task_finished, f"在 {max_wait} 秒内未检测到任务完成标识。当前流日志: {text_stream[:300]}"
    print("[5/5] ✅ 实时日志捕获与任务闭环验证成功！")
    print(f"      📝 捕获日志片段样例:\n" + "\n".join("      " + l for l in text_stream.splitlines()[-6:]))

    # 关闭测试服务
    stop_stream.set()
    httpd.shutdown()
    print("\n🎉🎉🎉 E2E 端到端测试 100% 通过！纯 Python 控制台架构稳定可靠！\n")

if __name__ == "__main__":
    run_e2e_test()
