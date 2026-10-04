@echo off
chcp 65001 >nul
title LearnUs 个人知识库助手 (学霸模式自动同步)

echo ======================================================================
echo   🎓 延世大学 LearnUs 智能学习助手 & 知识库自动化管理系统
echo ======================================================================
echo.
echo  [1] 自动登录并拉取本学期课程
echo  [2] 按课程、按单元 (周次) 归类下载所有课件、资料附件与视频
echo  [3] 自动将 .doc / .docx 转换为结构化 Markdown (.md)
echo  [4] 本地调用阿里 SenseVoice 提取外语课堂实录讲义 (*.transcript.md)
echo  [5] 自动生成各级 README.md 知识树索引供 AI Agent (Harness) 深度阅读
echo.
echo ----------------------------------------------------------------------
echo  正在通过 WSL2 (Ubuntu-Agent) 调用本地 Python 与 RTX 显卡环境...
echo ----------------------------------------------------------------------
echo.

wsl.exe -d Ubuntu-Agent -u miner --cd /mnt/g/project/learnus-helper /home/miner/subtitle_pipeline/.venv/bin/python learnus.py sync %*

echo.
echo ======================================================================
echo  任务执行完毕，按任意键退出窗口...
echo ======================================================================
pause >nul
