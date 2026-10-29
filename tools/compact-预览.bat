@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 记忆库整理 —— 预演模式（只出方案，不改任何文件）
echo.
"D:\qqbot\_uv_tools\astrbot\Scripts\python.exe" -X utf8 "%~dp0compact_memory.py" %*
echo.
pause
