@echo off
chcp 65001 >nul
title SnowLuma (QQ OneBot protocol side)
cd /d "%~dp0snowluma"
echo ============================================================
echo  SnowLuma runtime
echo  WebUI : http://127.0.0.1:5099
echo  OneBot: http://127.0.0.1:3000/  ws://127.0.0.1:3001/
echo  Close this window to stop SnowLuma.
echo ============================================================
echo.
".\node.exe" ".\index.mjs"
echo.
echo SnowLuma exited. Press any key to close.
pause >nul
