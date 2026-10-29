@echo off
chcp 65001 >nul
title AstrBot (LLM bot framework)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "PATH=C:\Users/<user>\.pyffmpeg\bin;%PATH%"
cd /d "%~dp0astrbot"
echo ============================================================
echo  AstrBot
echo  WebUI          : http://127.0.0.1:6185
echo  OneBot reverse : ws://127.0.0.1:6199/ws  (SnowLuma connects here)
echo  Close this window to stop AstrBot.
echo ============================================================
echo.
"%~dp0_uv_tools\astrbot\Scripts\python.exe" -m astrbot.cli.__main__ run
echo.
echo AstrBot exited. Press any key to close.
pause >nul
