@echo off
chcp 65001 >nul
title 启动 LearnUs 智能助学工作台

:: 无黑框静默拉起 PowerShell WPF 客户端
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0LearnUsHelper.ps1"
exit
