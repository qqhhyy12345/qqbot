# 一次性工具：在桌面创建「<BOT_NICK>助手」快捷方式（指向 D:\qqbot\start-all.bat）
import os

import win32com.client

ws = win32com.client.Dispatch("WScript.Shell")
lnk = ws.CreateShortCut(r"C:\Users/<user>\Desktop\<BOT_NICK>助手.lnk")
lnk.TargetPath = r"D:\qqbot\start-all.bat"
lnk.WorkingDirectory = r"D:\qqbot"
lnk.Description = "启动QQ机器人（SnowLuma + AstrBot）"
lnk.IconLocation = r"C:\Windows\System32\shell32.dll, 14"
lnk.WindowStyle = 1
lnk.Save()

p = r"C:\Users/<user>\Desktop\<BOT_NICK>助手.lnk"
print("exists:", os.path.exists(p), os.path.getsize(p), "bytes")
chk = ws.CreateShortCut(p)
print("target:", chk.TargetPath)
print("workdir:", chk.WorkingDirectory)
