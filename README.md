# learnus-helper (LearnUs 智能助学知识库助手)

延世大学 LearnUs (ys.learnus.org) 自动化学习辅助与知识库构建系统。

**像一个管理的井井有条的学霸一样**：
1. 自动登录延世门户，列出未交作业与待办事项。
2. 自动建立规范学习知识库文件夹，按课程、按周次/单元分类整理全部课件、资料室文件与录播视频。
3. **课件文档自动转 Markdown**：自动将 `.docx` / `.doc` 转换为排版清晰的 `.md`，方便各类 AI Harness (Claude Code, Cursor, Codex 等) 深入阅读和分析。
4. **视频讲义极速提取（纯外语原文）**：通过 WSL2 本地 RTX 显卡与阿里开源 **SenseVoice** 模型，秒级提取韩语/英语课堂实录讲义（`*.transcript.md`），带时间戳分段，原汁原味，不耗云端 token。
5. **全自动索引树**：自动为每个单元、每门课及整个工作区生成结构化 `README.md`，形成完整的 AI 辅助学习知识库。

---

## 快速使用

### 方式一：Windows 用户一键启动（最推荐）
在 `G:\project\learnus-helper` 目录下，直接双击：
```cmd
sync_workspace.bat
```
*(脚本会自动通过 WSL2 调用已配置好的 Python 环境和本地显卡模型，无需在 Windows 宿主机繁琐配置环境)*

### 方式二：命令行运行
```bash
# 1.【学霸模式全自动同步】(下载课件 + 视频 + doc转md + SenseVoice提取外语讲义 + 生成知识库)
python learnus.py sync

# 2. 仅同步某一门课 (根据课程名或ID过滤)
python learnus.py sync --course "컴퓨터네트워크"

# 3. 仅下载课件和资料，不下载大型视频
python learnus.py sync --no-videos

# 4. 首次登录延世门户 SSO 并保存凭据 (免后续重复输入密码)
python learnus.py login --save

# 5. 查看待办未交作业与未出席视频 (并输出 report.html)
python learnus.py todo

# 6. 对已有下载文件夹重新跑文档格式转换与知识库索引构建
python learnus.py reindex
```

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
