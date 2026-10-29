@echo off
chcp 65001 >nul
title QQ Bot - start all
echo Starting SnowLuma and AstrBot in separate windows...
start "SnowLuma" cmd /c "%~dp0start-snowluma.bat"
timeout /t 10 /nobreak >nul
start "AstrBot" cmd /c "%~dp0start-astrbot.bat"
timeout /t 10 /nobreak >nul
start "" "http://127.0.0.1:5099"
start "" "http://127.0.0.1:6185"
echo Both services launched. Two windows should stay open; do not close them.
echo If QQ is not running yet, start QQ and log in with the bot account.
timeout /t 8 /nobreak >nul
