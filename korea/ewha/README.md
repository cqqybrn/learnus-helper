# 梨花女子大学 · CyberCampus + K-MOOC

`cyber.ewha.ac.kr`（Eureka SSO + 邮箱 MFA）与 **K-MOOC 学分课**（`lms.kmooc.kr`）一体工具。  
Cyber 上很多课只有公告/壳进度，**真实 VOD 在 K-MOOC**；本目录为原 `ewha-helper` 全栈迁入。

## 安装

在仓库根目录或本目录：

```bash
pip install -r requirements.txt
pip install -r ../../requirements.txt   # 根目录 learnus 共用依赖
playwright install chromium
```

需 **Node.js**（Eureka 登录页 SEED 加密）。

## 环境变量

| 变量 | 用途 |
|------|------|
| `EWHA_ID` / `EWHA_PW` | Cyber 学号 / 门户密码 |
| `KMOOC_ID` / `KMOOC_PW` | K-MOOC 登录（常与 Ewha 邮箱相同） |
| `KMOOC_COURSE` | LMS 课程 id，如 `20514`、`20515` |

## CyberCampus（`ewha.py`）

```bash
cd korea/ewha

# 登录（邮箱验证码；或分步 MFA）
python ewha.py login
python mfa_login_once.py start
python mfa_login_once.py finish 123456

python ewha.py courses
python ewha.py todo
python ewha.py todo --out report.html

# Cyber 内 VOD 倍速出勤
python ewha.py watch --speed 2.0
python ewha.py watch --course "반도체" --limit 3

# 知识库：课件下载 + doc/ppt/pdf → md + README 索引
python ewha.py sync
python ewha.py sync --course "반도체" --no-docs
python ewha.py download --out ./workspace --convert-docs
python ewha.py reindex

# K-MOOC 子命令（学籍同步说明、与 kmooc.py 联动）
python ewha.py kmooc --help
python ewha.py kmooc --sync --sync-only --id 146858
```

会话：`.ewha.cookies`（勿提交 git）。

## K-MOOC 真播（`kmooc.py`）

```bash
python kmooc.py search --q "반도체"
python kmooc.py watch --course "반도체" --speed 2.0
python kmooc_watch_pending.py   # LMS pending 列表 + headed/无头续播
```

## K-MOOC 轻量心跳（不解码，`kmooc/`）

低内存、按 `trackDetail` 约 60s 一步，见 [kmooc/README.md](kmooc/README.md)（或下方摘要）：

```bash
cd kmooc
python kmooc_status.py
python kmooc_heartbeat_paced.py
python kmooc_heartbeat_all.py
python kmooc_monitor_loop.py
```

## 项目结构

```text
korea/ewha/
├── ewha.py                 # Cyber 主 CLI
├── kmooc.py                # K-MOOC 搜索 / 真播
├── mfa_login_once.py       # MFA 分步登录
├── kmooc_watch_pending.py  # LMS pending 播放
├── kmooc_relogin.py        # K-MOOC 会话重登
├── kmooc_play_one.py       # 单条试播
├── kmooc/                  # 逆向轻量刷课
└── requirements.txt        # 梨花侧 Python 依赖
```

## 说明

- **Cyber Online-Attendance 空表** 的壳课不会写入 K-MOOC 视频进度；学分以 K-MOOC + 이수증为准。
- 轻量刷课墙钟时间约等于剩余片长；_sesskey 长跑见 `kmooc_heartbeat_all.py` 自动刷新。
