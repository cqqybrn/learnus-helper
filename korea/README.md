# 韩国大学 LMS 轻量刷课 / 出勤模块

本目录按学校拆分 **Coursemos / Moodle VOD** 逆向出的 **低内存进度上报**（不依赖全片解码，按播放器心跳规则 POST）。

| 学校 | 平台 | 目录 | 上报方式 |
|------|------|------|----------|
| 延世 | LearnUs `ys.learnus.org` | [yonsei/](yonsei/) | `type=vod_log` |
| 梨花 · K-MOOC 学分课 | `lms.kmooc.kr` | [ewha/kmooc/](ewha/kmooc/) | `coursemostype=trackDetail` |

根目录 `learnus.py` 仍为延世 **2x 无头真播** 主入口；轻量模式见上表各 README。

后续计划：在同仓库 `korea/<school>/` 追加更多校网。
