@echo off
chcp 65001 >nul
title QQ Bot Launcher
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch-bot.ps1" %*
