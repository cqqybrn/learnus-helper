# 梨花 · K-MOOC LMS（`lms.kmooc.kr`）

与 CyberCampus 壳课分离：**视频进度只写在 K-MOOC**，需在本平台刷满后再做满意度 / 이수증。

## 环境变量

- `KMOOC_ID` / `KMOOC_PW`（默认 ID 可为学校邮箱）
- `KMOOC_COURSE`：LMS 课程 id，如 `20514` / `20515`

## 命令

```bash
cd korea/ewha/kmooc

# 单条 pending 以下约 60s 一步心跳（墙钟≈剩余片长）
set KMOOC_VOD=2326506
python kmooc_heartbeat_paced.py

# 自动刷当前课全部 pending + 日志
python kmooc_heartbeat_all.py

# 每 5 分钟写一次进度快照（另开终端）
set KMOOC_MONITOR_MIN=5
python kmooc_monitor_loop.py

# 仅查 pending
python kmooc_status.py
```

## 机制摘要

- `POST /mod/vod/action.php`，`coursemostype=trackDetail`，`state=8` 约每 60s。
- 起点用 **ubonattend 报告 % × 时长**，勿用页面续播点盲跳片尾。
- 长跑需刷新 `sesskey`（`kmooc_heartbeat_all.py` 已处理 399）。
