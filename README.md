# learnus-helper · 韩国大学 LMS 助手

以 **延世 LearnUs** 为主入口，并逐步在 [`korea/`](korea/) 聚合各校 **Coursemos / Moodle** 工具（梨花 K-MOOC、轻量 VOD 心跳刷课等）。

**像一个管理的井井有条的学霸一样**：
1. 自动登录延世门户，列出未交作业与待办事项。
2. 自动建立规范学习知识库文件夹，按课程、按周次/单元分类整理全部课件、资料室文件与录播视频。
3. **课件文档自动转 Markdown**：自动将 `.docx` / `.doc` 转换为排版清晰的 `.md`，方便各类 AI Harness (Claude Code, Cursor, Codex 等) 深入阅读和分析。
4. **视频讲义极速提取（纯外语原文）**：通过 WSL2 本地 RTX 显卡与阿里开源 **SenseVoice** 模型，秒级提取韩语/英语课堂实录讲义（`*.transcript.md`），带时间戳分段，原汁原味，不耗云端 token。
5. **全自动索引树**：自动为每个单元、每门课及整个工作区生成结构化 `README.md`，形成完整的 AI 辅助学习知识库。

---

## 快速使用

### 方式一：纯 Python 原生 Web 控制台（推荐，零外部前端依赖）
直接启动 Web UI 客户端（全平台通用，自动弹出浏览器）：
```bash
# Windows 用户直接双击：
启动学习助手(纯Python版).bat

# 或命令行直接运行：
python learnus_ui.py
```
*(现代化深色 Web 界面，SSE 实时日志流，支持一键知识库同步、一键 2.0 倍速出勤、查看待办任务)*

### 方式二：Windows 桌面客户端（WPF 原版）
在 Windows 目录下，双击：
```cmd
启动学习助手.bat
```

### 方式三：命令行运行 (CLI)
```bash
# 1.【2.0 倍速自动浏览出勤】(自动查找所有未看/未达标视频，安全心跳无头挂机)
python learnus.py watch --speed 2.0

# 2.【学霸模式全自动同步】(下载课件 + 视频 + doc转md + SenseVoice提取外语讲义 + 生成知识库)
python learnus.py sync

# 3. 仅针对某门课同步或出勤 (例如: 채플 / 소설쓰기)
python learnus.py watch --course "채플" --speed 2.0
python learnus.py sync --course "채플"

# 4. 仅下载课件和资料，不下载大型视频
python learnus.py sync --no-videos

# 5. 查看待办未交作业与未出席视频 (并输出 report.html)
python learnus.py todo

# 6. 对已有下载文件夹重新跑文档格式转换与知识库索引构建
python learnus.py reindex
```

### 轻量 VOD 刷课（逆向心跳，低内存）

见 [`korea/README.md`](korea/README.md)：

- **延世**：`korea/yonsei/vod_log_heartbeat_paced.py`（`vod_log`）
- **梨花全栈**：`korea/ewha/ewha.py`（Cyber 登录/MFA/todo/sync/watch）、`kmooc.py`、[`korea/ewha/kmooc/`](korea/ewha/kmooc/) 轻量心跳

---

## 知识库目录结构 (Harness-Friendly)

执行 `sync` 后生成的工作区结构：
```text
workspace/
├── README.md                      <-- 知识库总目录：全学期课程一览、紧急待办看板、Agent 指引
└── 컴퓨터네트워크/                 <-- 课程目录
    ├── README.md                  <-- 课程大纲：教学进度、单元导航表、作业清单
    ├── 01주차/                     <-- 单元文件夹 (按周分类)
    │   ├── README.md              <-- 单元指南：本周课件清单、视频讲义索引
    │   ├── syllabus.docx          <-- 原始课件文档
    │   ├── syllabus.md            <-- [自动转换] 结构化 Markdown 供 Harness 深度解析
    │   ├── lecture_01.mp4         <-- 下载的课堂录播视频
    │   ├── lecture_01.raw.srt     <-- 原始外挂字幕
    │   └── lecture_01.transcript.md <-- [SenseVoice] 提取的外语原汁原味课堂发言稿 (带时间标记)
    └── 02주차/
```

---

## 环境与依赖

* **Python 依赖**：`requests`, `beautifulsoup4`, `python-docx`, `websocket-client`
* **视频处理**：`ffmpeg` (用于下载 m3u8 流媒体)
* **语音转写**：WSL2 `Ubuntu-Agent` 内部的阿里 `SenseVoiceSmall` 模型 (纯本地推理，无需联网 LLM API)
