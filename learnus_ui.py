#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LearnUs Helper - 纯 Python 原生 Web UI 客户端
提供无依赖、全平台的轻量浏览器交互界面。
无需 PowerShell、无需 Node.js、无需第三方外部库（基于 Python 原生 http.server + SSE 流式日志）。
"""

import http.server
import json
import os
import queue
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

PORT = 8088
HERE = Path(__file__).resolve().parent

# 全局任务运行状态
task_lock = threading.Lock()
current_process = None
log_queue = queue.Queue()
task_status = {"running": False, "command": "", "exit_code": None}

def run_task(cmd_args: list):
    global current_process, task_status
    with task_lock:
        if task_status["running"]:
            return False, "已有任务正在运行中"
        task_status["running"] = True
        task_status["command"] = " ".join(cmd_args)
        task_status["exit_code"] = None

    def worker():
        global current_process, task_status
        cmd = [sys.executable, str(HERE / "learnus.py")] + cmd_args
        log_queue.put(f"[*] 🚀 启动任务: {' '.join(cmd_args)}\n")
        log_queue.put(f"[*] 执行路径: {HERE}\n")
        log_queue.put("=" * 60 + "\n\n")

        try:
            current_process = subprocess.Popen(
                cmd,
                cwd=str(HERE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace"
            )

            for line in iter(current_process.stdout.readline, ""):
                log_queue.put(line)
            current_process.stdout.close()
            ret = current_process.wait()
            task_status["exit_code"] = ret
            log_queue.put(f"\n[+] 任务完成 (退出码: {ret})\n")
        except Exception as e:
            log_queue.put(f"\n[!] 任务异常: {e}\n")
            task_status["exit_code"] = -1
        finally:
            with task_lock:
                task_status["running"] = False
                current_process = None

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return True, "任务已启动"

def stop_task():
    global current_process
    with task_lock:
        if current_process and current_process.poll() is None:
            current_process.terminate()
            time.sleep(0.5)
            if current_process.poll() is None:
                current_process.kill()
            log_queue.put("\n[!] ⚠️ 用户手动终止了当前任务。\n")
            task_status["running"] = False
            return True
    return False

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LearnUs 智能助学知识库 - 纯 Python 控制台</title>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #111827;
            --border-color: #1f2937;
            --text-main: #f9fafb;
            --text-muted: #9ca3af;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --purple: #8b5cf6;
            --purple-hover: #7c3aed;
            --danger: #ef4444;
            --danger-hover: #dc2626;
            --success: #10b981;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); min-height: 100vh; padding: 24px; display: flex; flex-direction: column; }
        .container { max-width: 1000px; width: 100%; margin: 0 auto; flex: 1; display: flex; flex-direction: column; gap: 16px; }
        
        .header { display: flex; justify-content: space-between; align-items: center; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 18px 24px; }
        .header-title h1 { font-size: 1.3rem; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }
        .header-title p { font-size: 0.85rem; color: var(--text-muted); margin-top: 4px; }
        .status-badge { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; font-weight: 600; padding: 6px 14px; border-radius: 20px; background: #1e293b; border: 1px solid #334155; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--success); }
        .status-dot.running { background: #f59e0b; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

        .toolbar { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 16px 20px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
        .input-group { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 280px; }
        .input-group label { font-size: 0.88rem; color: var(--text-muted); white-space: nowrap; }
        .input-group input { flex: 1; background: #1f2937; border: 1px solid #374151; color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 0.9rem; outline: none; }
        .input-group input:focus { border-color: var(--primary); }

        .actions-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
        .action-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 16px; display: flex; justify-content: space-between; align-items: center; }
        .action-info h3 { font-size: 0.95rem; font-weight: 600; color: #fff; margin-bottom: 4px; }
        .action-info p { font-size: 0.78rem; color: var(--text-muted); line-height: 1.3; }

        button { cursor: pointer; border: none; font-weight: 600; font-size: 0.88rem; padding: 9px 16px; border-radius: 6px; transition: all 0.15s ease; color: #fff; white-space: nowrap; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-primary { background: var(--primary); }
        .btn-primary:hover:not(:disabled) { background: var(--primary-hover); }
        .btn-purple { background: var(--purple); }
        .btn-purple:hover:not(:disabled) { background: var(--purple-hover); }
        .btn-secondary { background: #374151; }
        .btn-secondary:hover:not(:disabled) { background: #4b5563; }
        .btn-danger { background: var(--danger); }
        .btn-danger:hover:not(:disabled) { background: var(--danger-hover); }

        .console-container { flex: 1; display: flex; flex-direction: column; background: #030712; border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden; min-height: 380px; }
        .console-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: #111827; border-bottom: 1px solid var(--border-color); font-size: 0.8rem; color: var(--text-muted); }
        .console-body { flex: 1; padding: 14px; overflow-y: auto; font-family: "JetBrains Mono", "Cascadia Code", Consolas, Menlo, monospace; font-size: 0.84rem; line-height: 1.5; color: #d1d5db; white-space: pre-wrap; word-break: break-all; }
    </style>
</head>
<body>
    <div class="container">
        <!-- 头部 -->
        <div class="header">
            <div class="header-title">
                <h1>🎓 LearnUs 智能知识库助手 <span style="font-size: 0.75rem; background: #1e3a8a; color: #93c5fd; padding: 2px 8px; border-radius: 12px;">纯 Python 原生版</span></h1>
                <p>Yonsei LMS 自动学霸整理系统 & 无头自动化浏览出勤</p>
            </div>
            <div class="status-badge">
                <div id="statusDot" class="status-dot"></div>
                <span id="statusLabel">就绪</span>
            </div>
        </div>

        <!-- 课程过滤栏 -->
        <div class="toolbar">
            <div class="input-group">
                <label for="courseInput">🎯 目标课程过滤：</label>
                <input type="text" id="courseInput" placeholder="留空则处理全部课程 (例如: 채플 或 고전문학)">
            </div>
            <button class="btn-secondary" id="btnTodo" onclick="triggerTask('todo')">📋 查看待办与出勤</button>
            <button class="btn-secondary" onclick="openFolder()">📂 打开桌面知识库</button>
        </div>

        <!-- 核心操作卡片 -->
        <div class="actions-grid">
            <div class="action-card" style="border-left: 4px solid var(--primary);">
                <div class="action-info">
                    <h3>📚 学霸模式全自动同步 (Sync)</h3>
                    <p>下载课件+视频、DOC/PPTX/PDF转MD、SenseVoice离线讲义提取、三级README树</p>
                </div>
                <button class="btn-primary" id="btnSync" onclick="triggerTask('sync')">▶ 开始同步</button>
            </div>

            <div class="action-card" style="border-left: 4px solid var(--purple);">
                <div class="action-info">
                    <h3>⚡ 自动浏览与出勤挂机 (Watch 2.0x)</h3>
                    <p>无头无声后台模拟 2.0 倍速真实心跳上报，自动按顺序看完未达标视频</p>
                </div>
                <button class="btn-purple" id="btnWatch" onclick="triggerTask('watch')">🚀 开始挂机</button>
            </div>
        </div>

        <!-- 终端输出 -->
        <div class="console-container">
            <div class="console-header">
                <span>实时运行日志 (Terminal Stream)</span>
                <div>
                    <button class="btn-danger" id="btnStop" style="display: none; padding: 4px 10px; font-size: 0.75rem;" onclick="stopCurrentTask()">⏹ 终止当前任务</button>
                    <button class="btn-secondary" style="padding: 4px 10px; font-size: 0.75rem; margin-left: 6px;" onclick="clearLogs()">清屏</button>
                </div>
            </div>
            <div class="console-body" id="consoleLogs">欢迎使用 LearnUs 纯 Python 客户端控制台。请点击上方按钮开始任务...&#10;</div>
        </div>
    </div>

    <script>
        const statusDot = document.getElementById('statusDot');
        const statusLabel = document.getElementById('statusLabel');
        const consoleLogs = document.getElementById('consoleLogs');
        const btnSync = document.getElementById('btnSync');
        const btnWatch = document.getElementById('btnWatch');
        const btnTodo = document.getElementById('btnTodo');
        const btnStop = document.getElementById('btnStop');

        function updateUIState(running) {
            if (running) {
                statusDot.className = 'status-dot running';
                statusLabel.innerText = '正在运行任务...';
                btnSync.disabled = true;
                btnWatch.disabled = true;
                btnTodo.disabled = true;
                btnStop.style.display = 'inline-block';
            } else {
                statusDot.className = 'status-dot';
                statusLabel.innerText = '就绪';
                btnSync.disabled = false;
                btnWatch.disabled = false;
                btnTodo.disabled = false;
                btnStop.style.display = 'none';
            }
        }

        function triggerTask(action) {
            const course = document.getElementById('courseInput').value.trim();
            fetch('/api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action, course: course })
            }).then(r => r.json()).then(data => {
                if (!data.ok) alert(data.msg);
            });
        }

        function stopCurrentTask() {
            fetch('/api/stop', { method: 'POST' });
        }

        function openFolder() {
            fetch('/api/open_folder', { method: 'POST' });
        }

        function clearLogs() {
            consoleLogs.innerText = '';
        }

        // SSE 监听服务器日志流
        const evtSource = new EventSource('/api/logs');
        evtSource.onmessage = function(e) {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'log') {
                    consoleLogs.innerText += data.content;
                    consoleLogs.scrollTop = consoleLogs.scrollHeight;
                } else if (data.type === 'status') {
                    updateUIState(data.running);
                }
            } catch(err) {}
        };
    </script>
</body>
</html>
"""

class LearnUsWebHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 静默常规 HTTP 请求日志
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif url.path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            # 先推送当前状态
            status_init = json.dumps({"type": "status", "running": task_status["running"]})
            self.wfile.write(f"data: {status_init}\n\n".encode("utf-8"))
            self.wfile.flush()

            last_running = task_status["running"]
            while True:
                try:
                    # 检查是否有新日志
                    try:
                        line = log_queue.get(timeout=0.25)
                        payload = json.dumps({"type": "log", "content": line})
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        pass

                    # 检查状态变化
                    cur_running = task_status["running"]
                    if cur_running != last_running:
                        last_running = cur_running
                        payload = json.dumps({"type": "status", "running": cur_running})
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        else:
            self.send_error(404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            req_data = json.loads(post_body)
        except Exception:
            req_data = {}

        if url.path == "/api/run":
            action = req_data.get("action", "")
            course = req_data.get("course", "").strip()

            args = []
            if action == "sync":
                args = ["sync"]
            elif action == "watch":
                args = ["watch", "--speed", "2.0"]
            elif action == "todo":
                args = ["todo"]

            if course:
                args.extend(["--course", course])

            ok, msg = run_task(args)
            self._send_json({"ok": ok, "msg": msg})

        elif url.path == "/api/stop":
            ok = stop_task()
            self._send_json({"ok": ok})

        elif url.path == "/api/open_folder":
            desktop_kb = Path.home() / "Desktop" / "Chapel_KnowledgeBase"
            if not desktop_kb.exists():
                desktop_kb = HERE / "workspace"
                desktop_kb.mkdir(parents=True, exist_ok=True)
            try:
                if sys.platform == "win32":
                    os.startfile(str(desktop_kb))
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(desktop_kb)])
                else:
                    subprocess.run(["xdg-open", str(desktop_kb)])
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "msg": str(e)})
        else:
            self.send_error(404)

    def _send_json(self, data: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="LearnUs Helper 纯 Python 原生 Web UI 控制台")
    parser.add_argument("--port", type=int, default=PORT, help=f"Web 端口 (默认: {PORT})")
    parser.add_argument("--no-browser", action="store_true", help="不自动弹出打开浏览器")
    args = parser.parse_args()

    port = args.port
    # 允许端口快速重用
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", port), LearnUsWebHandler) as httpd:
        url = f"http://127.0.0.1:{port}"
        print(f"\n=======================================================")
        print(f" 🚀 LearnUs 纯 Python 控制台已启动: {url}")
        print(f"    - 原生跨平台: Windows / macOS / Linux / WSL")
        print(f"    - 零外部前端依赖 (纯标准库开发)")
        print(f"=======================================================\n")
        if not args.no_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n正在关闭控制台服务...")

if __name__ == "__main__":
    main()
