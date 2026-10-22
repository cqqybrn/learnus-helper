# K-MOOC LMS 轻量刷课（`lms.kmooc.kr`）

与 [../README.md](../README.md) 中 `kmooc.py` / `kmooc_watch_pending.py`（前端真播）互补。  
本模块采用**协议层逆向心跳**方式，直接与 LMS 交互，无需音频/视频解码，低内存且适合在后台长期运行。

---

## 快速使用

### 1. 凭证配置（二选一）

**方式 A：本地配置文件（推荐，已加入 `.gitignore` 防止泄露）**  
在 `korea/ewha/kmooc/credentials.json` 创建文件：
```json
{
  "KMOOC_ID": "your_id@ewha.ac.kr",
  "KMOOC_PW": "your_password",
  "KMOOC_COURSE": "20515"
}
```

**方式 B：环境变量注入**
```bash
# Linux / WSL
export KMOOC_ID="your_id@ewha.ac.kr"
export KMOOC_PW="your_password"
export KMOOC_COURSE="20515"

# Windows (CMD / PowerShell)
set KMOOC_ID=your_id@ewha.ac.kr
set KMOOC_PW=your_password
set KMOOC_COURSE=20515
```

### 2. 运行命令

```bash
# 1. 检查当前课程所有 VOD 达标情况
python3 kmooc_status.py

# 2. 全自动无人值守连续刷课（全量循环）
python3 kmooc_heartbeat_all.py

# 3. 单视频精准步进（调试或补刷）
export KMOOC_VOD=2326506
python3 kmooc_heartbeat_paced.py

# 4. 后台定时监控出勤日志（每 5 分钟轮询）
python3 kmooc_monitor_loop.py
```

---

## 运行机制

- **协议追踪**：向 `POST /mod/vod/action.php` 持续发送 `coursemostype=trackDetail` 数据包。
- **状态流转**：
  - `state=3`：初始化播放起点（由出勤报告已完成 % × 总时长精准确定）。
  - `state=8`：每 63 秒发送一次步进心跳（每次推进 60 秒进度，匹配服务端防作弊时间窗）。
  - `state=10`：到达终点时提交完成信令，并在出勤报告表复查是否达成 100%。

---

## 长任务踩坑与加固总结

在连续运行数小时的大规模刷课实战中，我们遇到了以下关键问题并完成了底层加固：

### 1. 会话过期与 `RuntimeError: no amd.progress` 崩溃
* **现象**：脚本在挂机数小时后，在某次切课或周期刷新（`refresh_track_session`）时抛出 `RuntimeError: no amd.progress`，导致整个后台进程中断退出。
* **根因**：K-MOOC 会话 Cookie 具有时效性。当 Session 过期时，请求 `viewer.php` 会被 302 重定向至登录页；或者因网络波动页面尚未渲染出内联脚本，导致正则表达式未能命中 `amd.progress(...)`。
* **加固修复**：
  - 将导航等待条件设为 `wait_until="networkidle"` 并附加安全渲染缓冲。
  - 为会话获取与刷新函数封装了 **3 次带鉴权的自动重试机制**：一旦捕获到解析异常，自动静默触发 `login()` 重新获取最新 Session 与 Cookie，然后恢复原任务继续执行，实现真正的无人值守。

### 2. 表单密钥失效（`code: 399`）死循环
* **现象**：服务端返回 `{'code': '399', 'msg': 'sesskey가 틀렸기 때문에 서식이 접수되지 않음!'}`。
* **根因**：连续心跳跨周期时服务端的 CSRF 令牌（`sesskey`）发生轮换，若继续沿用旧密钥发送心跳会被持续拒绝。
* **加固修复**：在检测到 `code == 399` 时，立即触发自动续登与 Session 刷新，重新从 HTML 中抓取最新 `sesskey` 并同步给全局 HTTP Session，防止死锁。

### 3. `<video>` 时长获取延迟与 Fallback 偏差
* **现象**：在 Linux 无头浏览器环境下，部分较慢的节点加载 `<video>` DOM 标签超过 40 秒，超时后触发了旧版本硬编码的备用时长（1327 秒），导致短视频按长视频跑，步数虚高。
* **根因**：Moodle 播放器通过动态 JS 将音视频流挂载至 iframe 或 blob，无头模式下直接抓取视频标签耗时不稳定。
* **加固修复**：
  - 将出勤报告页（`report.php`）的解析逻辑升级，同步正则抓取出勤表格里的**标准课时时间**（如 `15:34` 即 934 秒）。
  - `<video>` 标签探测时间缩短为 15 秒；若超时未就绪，直接使用表格提取的真实秒数作为精准 fallback，杜绝额外等待且保证 100% 步数吻合。

### 4. Linux (WSL) 原生后台守护运行
* **改进**：支持在 Ubuntu WSL 环境下使用 Linux 原生无头 Chromium（Playwright）运行，不再强依赖 Windows GUI 桌面窗口与 PowerShell 前台，支持后台守护运行与日志持续重定向。
