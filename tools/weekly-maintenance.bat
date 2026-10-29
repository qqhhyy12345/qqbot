@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\qqbot\_uv_tools\astrbot\Scripts\python.exe"
set "TMP=D:\qqbot\astrbot\data\temp"

echo ============================================
echo  记忆维护（每周一次）
echo  0. 备份核心数据（数据库 + 记忆 + 协议端配置）
echo  1. 清理 3 天前的临时音频与图片
echo  2. 私聊记忆脱敏提升进 people
echo  3. 记忆库整合（达阈值才动）
echo ============================================
echo.

echo [0/4] 备份核心数据...
"%PY%" -X utf8 backup_core.py
echo.

echo [1/4] 清理临时文件...
forfiles /p "%TMP%" /m edge_tts* /d -3 /c "cmd /c del @path" >nul 2>&1
forfiles /p "%TMP%" /m media_* /d -3 /c "cmd /c del @path" >nul 2>&1
echo      完成
echo.

echo [2/4] 脱敏提升...
"%PY%" -X utf8 promote_people.py --apply
echo.
echo [3/4] 记忆整合...
"%PY%" -X utf8 compact_memory.py --apply
echo.

echo 完成。报告在 reports\ 里。
pause
