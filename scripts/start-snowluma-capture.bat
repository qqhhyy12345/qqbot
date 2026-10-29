@echo off
chcp 65001 >nul
cd /d "%~dp0snowluma"
node.exe index.mjs > "%~dp0logs\snowluma-creds.out" 2>&1
