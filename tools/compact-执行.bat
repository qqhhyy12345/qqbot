@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 记忆库整理 —— 执行模式
echo 会先备份到 bot-memory\_backup\，删除的文件进 _archive\，可回滚
echo.
"D:\qqbot\_uv_tools\astrbot\Scripts\python.exe" -X utf8 "%~dp0compact_memory.py" --apply %*
echo.
pause
