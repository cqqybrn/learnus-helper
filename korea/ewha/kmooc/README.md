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

### 2. 核心运行命令

```bash
# 1. 检查当前课程所有 VOD 达标情况与待办统计
python3 kmooc_status.py

# 2. 全自动无人值守连续刷课（全量循环 + 自动切课 + 断点续刷）
python3 kmooc_heartbeat_all.py

# 3. 单视频精准步进（调试或补刷）
export KMOOC_VOD=2326509
python3 kmooc_heartbeat_paced.py

# 4. 后台定时监控出勤日志（每 5 分钟轮询）
python3 kmooc_monitor_loop.py
```

---

## 核心运行机制

- **协议追踪**：向 `POST /mod/vod/action.php` 持续发送 `coursemostype=trackDetail` 数据包。
- **状态流转**：
  - `state=3`：初始化播放起点（由出勤报告已完成 % × 总时长精准确定，支持断点无缝续刷）。
  - `state=8`：每 63 秒发送一次步进心跳（每次推进 60 秒进度，匹配服务端防作弊时间窗）。
  - `state=10`：到达视频终点时提交完播信令，并在出勤报告表复查是否达成 100%。

---

## 实战踩坑与加固总结

在连续运行数小时的大规模刷课实战中，我们深入分析了 Moodle Coursemos VOD 系统的鉴权、状态机与反作弊机制，定位并彻底解决了以下核心问题：

### 1. 会话时效过期与 `RuntimeError: no amd.progress` 崩溃
* **现象**：脚本在挂机数小时后，在某次切课或周期刷新（`refresh_track_session`）时抛出 `RuntimeError: no amd.progress on viewer`，导致整个后台进程意外崩溃中断。
* **根因分析**：
  1. K-MOOC LMS 服务端 Session Cookie 生命周期有限（约 20-30 分钟无交互即过期）。
  2. 会话过期后，向 `viewer.php?id={cmid}` 发送请求会被 302 临时重定向至登录页 `https://www.kmooc.kr/login`。
  3. 登录页自然不包含播放器内联脚本 `amd.progress(...)`，正则表达式匹配失败直接导致抛错。
* **加固实现**：
  - 在 `get_track_session` 与 `refresh_track_session` 中封装了 **3 次带鉴权恢复的自动重试机制**。
  - 一旦捕获到匹配异常或页面加载失败，自动静默触发 `login(p)` 重新登录，抓取全新有效的 Session Cookie 与页面上下文，随后无缝恢复原刷课流程，杜绝长任务异常退出。

### 2. 表单 CSRF 密钥失效（`code: 399`）与心跳死锁
* **现象**：服务端返回错误数据包 `{'code': '399', 'msg': 'sesskey가 틀렸기 때문에 서식이 접수되지 않음!'}`，后续所有心跳均被拒收。
* **根因分析**：连续发送心跳跨越整点或周期性维护窗口时，服务端的 CSRF 防护令牌（`sesskey`）发生轮换。如果 Python 端 `requests.Session` 沿用旧密钥发送 `coursemostype=trackDetail`，请求会被安全网关判定为非法表单提交。
* **加固实现**：
  - 在全自动心跳主循环中，每 5 个 step（约 5 分钟）主动触发轻量 session 续期（`refresh_track_session`）。
  - 在步进中动态监控服务端的响应状态码；一旦发现返回 `code == '399'`，立即打断当前旧会话，强制触发自动重登拉取最新 `sesskey` 并同步给全局 HTTP Session，防止死循环与假死。

### 3. `<video>` DOM 延迟挂载与硬编码 Fallback 偏差
* **现象**：在 Linux (WSL) 无头环境下，偶发出现视频时长读取耗时过长甚至超时（>40s），超时后落入旧版本硬编码的 1327 秒备用时长，导致只有十几分钟的短视频虚增成二十多分钟跑，造成时间浪费。
* **根因分析**：Moodle Coursemos 播放器采用动态 JS 异步挂载流媒体元素，有时根据 CDN 调度挂载为 blob/iframe，DOM 探测不可靠。
* **加固实现**：
  - 深度解析出勤表格：在访问 `report.php?id={course_id}` 时，同步正则抓取出勤表每一行标注的**标准学时长度**（如 `15:34` 提取为 934 秒）。
  - 将无头 DOM 探针的最大等待时长缩短至 15 秒；若超时未捕获到 `<video>` 标签，直接将表格提取的标准秒数作为精确 fallback，实现步数与视频真实时长 100% 严格吻合。

### 4. 服务端防作弊与步进窗口校验（Paced Heartbeat 节奏控制）
* **现象**：若直接向接口发送一次性跳跃数据包（如直接发 `current=dur` 或直接触发 `state=10`），服务端返回成功（code 100），但出勤报告中的累计学习时长和出勤率 **保持 0% 不变**。
* **根因分析**：Coursemos 后端会在每条 `trackDetail` 中比对客户端上报的 `unixtime` 与服务端接收时间，以及前后两个数据包的 `positionfrom` 与 `positionto` 跨度。若步进增量超过物理流逝时间（例如 1 秒内推进了 60 秒），后端判定为非法作弊并不计入出勤有效时长。
* **加固实现**：
  - 严格采用 **60 秒步进 + 63 秒物理等待间隔**（`step=60.0s`, `interval=63.0s`），预留 3 秒网络与处理开销。
  - 状态流转序列规范化：`state=3`（起始对齐）→ 周期性 `state=8`（稳定匀速推进行程）→ `state=10`（终点确认与完播信令提交）。

### 5. 断点续刷与进度精准对齐
* **现象**：若某节课已手动观看或脚本中途被系统调度重启（例如出勤率显示 51.39%），从第 0 秒重跑会带来严重的效率浪费。
* **加固实现**：
  - 切入每门新课前，先解析当前项在 `report.php` 里的最新进度百分比。
  - 若已达 100% 直接自动跳过；若介于 0% 到 100% 之间，按 `start = int(dur * (pct / 100.0))` 自动定位到最近的一个整分钟步长，直接从该时间戳开始续刷。

### 6. Linux (WSL) 原生无人值守运行与凭据隔离
* **现象**：直接拉起无头 Chromium 时若缺少 Linux 依赖会导致进程直接崩溃退出；且在脚本内硬编码账号密码存在极高的 Git 泄漏风险。
* **加固实现**：
  - Playwright 启动参数增加 `--disable-gpu`, `--mute-audio`, `--no-sandbox` 等无头容器优化选项。
  - 建立凭证配置文件分层机制：优先读取同目录下的 `credentials.json`，次级回退环境变量。同时通过 `.gitignore` 彻底将敏感账号密码隔离在版本控制之外。
