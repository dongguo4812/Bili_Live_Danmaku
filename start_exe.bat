@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title B站直播互动控制中心
start "" "%~dp0Bili_Live_Danmaku.exe"
