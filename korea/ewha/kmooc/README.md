# K-MOOC LMS 轻量刷课（`lms.kmooc.kr`）

与 [../README.md](../README.md) 中 `kmooc.py` / `kmooc_watch_pending.py`（真播）互补。

## 命令

```bash
# 在 korea/ewha/kmooc 下
set KMOOC_ID=...@ewha.ac.kr
set KMOOC_PW=...
set KMOOC_COURSE=20515

python kmooc_status.py
python kmooc_heartbeat_paced.py
set KMOOC_VOD=2326506
python kmooc_heartbeat_all.py
python kmooc_monitor_loop.py
```

## 机制

- `POST /mod/vod/action.php`，`coursemostype=trackDetail`，`state=8` 约每 60s。
- 起点：**ubonattend 报告 % × 时长**。
- `code=399`：刷新 viewer 取新 `sesskey`（`kmooc_heartbeat_all.py`）。
