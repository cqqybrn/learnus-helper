# learnus-helper

延世大学 LearnUs (ys.learnus.org) 辅助脚本：列出未交作业和未出席视频，按周批量下载课程资料。

## 环境

- Python 3.8+（Windows / Linux 均可）
- `pip install -r requirements.txt`
- 可选：`ffmpeg`，仅在 `download --videos` 下载课程视频时需要
  - Ubuntu: `sudo apt install ffmpeg`
  - Windows: `winget install ffmpeg`

## 登录

```bash
python learnus.py login --save   # 延世门户 SSO 登录，--save 会把学号密码存进 credentials.json，过期后自动重新登录
```

不想存密码可以改用环境变量 `LEARNUS_ID` / `LEARNUS_PW`，或者从开了 `--remote-debugging-port=9222` 的 Chrome 里取 cookie：

```bash
python learnus.py cookie
```

`cookies.txt` 和 `credentials.json` 是登录凭证，已在 `.gitignore` 中排除，不要外传。

## 用法

```bash
python learnus.py todo                     # 未交作业 + 未出席视频，同时生成 report.html
python learnus.py download                 # 下载课程文件和资料室附件，按 课程/NN주차 分类
python learnus.py download --videos        # 同时下载课程视频（需要 ffmpeg）
python learnus.py download --course 299537 # 只处理一门课（课程 ID 或课程名的一部分）
```

其他选项：

- `--include-noncredit`：包含非学分（비교과）课程
- `--all-boards`：下载所有公告板的附件，而不只是资料室
- `--out DIR`：指定下载目录或报告路径

重复运行 `download` 只会下载新增的文件。
