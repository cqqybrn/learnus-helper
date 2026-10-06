@echo off
chcp 65001 >nul
title LearnUs 智能助学知识库 - 纯 Python 控制台
echo =======================================================
echo  正在启动 LearnUs 纯 Python Web 控制台...
echo  启动后将自动在默认浏览器中打开交互界面
echo =======================================================
wsl.exe -d Ubuntu-Agent -u miner --cd /mnt/g/project/learnus-helper /home/miner/subtitle_pipeline/.venv/bin/python learnus_ui.py
pause
