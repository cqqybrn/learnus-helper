# 延世 · LearnUs（`ys.learnus.org`）

与 K-MOOC 不同：进度为 **`type=vod_log`**，字段含 `track` / `attempt` / `logtime`；请求须带 **AJAX Referer**。

## 环境变量

- `LEARNUS_ID` / `LEARNUS_PW`（门户 SSO）
- `LEARNUS_COURSE`：Moodle 课程 id
- `LEARNUS_VOD`：活动 cm id（viewer `?id=`）

## 命令

```bash
# 单条轻量心跳（默认 2 步试跑，改 LEARNUS_MAX_STEPS=0 跑满）
set LEARNUS_VOD=4610335
python vod_log_heartbeat_paced.py

# 2x 真播（稳定、仓库主路径）
python learnus.py watch --speed 2.0
```

## 机制摘要

- 解析 viewer 内 `amd.progress(...)` → 见 `parse_vod_progress.py`。
- 播放器 `mod_vod/vod.js` 内 `c.ajax("8", pos, pos)` 对应 `vod_log`。
- `learningChecker` 在 `ys.learnus.org` 默认不连 socket，不影响本路径。
