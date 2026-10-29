# 一键启动 QQ 机器人（SnowLuma + AstrBot）
# 用法：双击桌面快捷方式；或 launch-bot.bat -CheckOnly 只体检不启动
param([switch]$CheckOnly)

$ErrorActionPreference = 'Continue'
$Root = 'D:\qqbot'
$QQExe = 'D:\QQ\QQ.exe'
$BotUin = '<BOT_QQ>'

function Test-Port([int]$port) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $c.Connect('127.0.0.1', $port)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Host ''
Write-Host '================ QQ 机器人启动 ================' -ForegroundColor Cyan
Write-Host ''

# ---------- 体检 ----------
$qq = Get-Process QQ -ErrorAction SilentlyContinue
$node = Get-Process node -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$Root\snowluma\*" }
$port3000 = Test-Port 3000
$port6185 = Test-Port 6185
$port6199 = Test-Port 6199

if ($qq) {
    Write-Host '[1/3] QQ 进程：在运行' -ForegroundColor Green
} else {
    Write-Host '[1/3] QQ 进程：没在运行（需要启动并登录小号）' -ForegroundColor Yellow
}

if ($node) {
    Write-Host '[2/3] SnowLuma：在运行' -ForegroundColor Green
} else {
    Write-Host '[2/3] SnowLuma：没在运行（需要启动）' -ForegroundColor Yellow
}
Write-Host "      OneBot 端口 3000：$(if ($port3000) { '已监听（小号已登录）' } else { '未监听' })" `
    -ForegroundColor $(if ($port3000) { 'Green' } else { 'DarkGray' })

if ($port6185) {
    Write-Host '[3/3] AstrBot：在运行' -ForegroundColor Green
} else {
    Write-Host '[3/3] AstrBot：没在运行（需要启动）' -ForegroundColor Yellow
}
Write-Host "      反向 WS 端口 6199：$(if ($port6199) { '已监听' } else { '未监听' })" `
    -ForegroundColor $(if ($port6199) { 'Green' } else { 'DarkGray' })

if ($CheckOnly) {
    Write-Host ''
    if ($port3000 -and $port6185 -and $port6199) {
        Write-Host '结论：链路已通，正常模式会直接打开两个面板。' -ForegroundColor Cyan
    } else {
        Write-Host '结论：正常模式会按上面的缺口逐个启动，并等待端口就绪。' -ForegroundColor Cyan
    }
    Write-Host '（这是检查模式，没有启动或关闭任何程序）' -ForegroundColor DarkGray
    exit 0
}

# ---------- QQ ----------
if (-not $qq) {
    if (Test-Path $QQExe) {
        Start-Process $QQExe
        Write-Host ''
        Write-Host "      已打开 QQ，请登录小号 $BotUin，登录后这里会自动继续（最多等 3 分钟）" -ForegroundColor Yellow
    } else {
        Write-Host "      找不到 $QQExe，请手动打开 QQ" -ForegroundColor Red
    }
}

# ---------- SnowLuma ----------
if (-not $node) {
    Write-Host ''
    Write-Host '      启动 SnowLuma...' -ForegroundColor Yellow
    Start-Process -FilePath "$Root\start-snowluma.bat" -WorkingDirectory $Root
}

$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    if (Test-Port 3000) { break }
    Start-Sleep -Seconds 3
}
if (-not (Test-Port 3000)) {
    Write-Host ''
    Write-Host '× 等了 3 分钟，OneBot 端口还是没开。' -ForegroundColor Red
    Write-Host '  多半是 QQ 没登录，或者登录的不是 <BOT_QQ>。' -ForegroundColor Red
    Write-Host '  看 SnowLuma 窗口里有没有 [Hook] login detected 这一行。' -ForegroundColor DarkGray
    Read-Host '  处理完按回车退出'
    exit 1
}
Write-Host '      QQ 已登录，OneBot 就绪' -ForegroundColor Green

# ---------- AstrBot ----------
if (-not $port6185) {
    Write-Host '      启动 AstrBot...' -ForegroundColor Yellow
    Start-Process -FilePath "$Root\start-astrbot.bat" -WorkingDirectory $Root
}

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if (Test-Port 6199) { break }
    Start-Sleep -Seconds 2
}
if (Test-Port 6199) {
    Write-Host '      AstrBot 就绪' -ForegroundColor Green
} else {
    Write-Host '      AstrBot 还没起来，去它的窗口看报错' -ForegroundColor Red
}

Start-Sleep -Seconds 2
Start-Process 'http://127.0.0.1:5099'
Start-Process 'http://127.0.0.1:6185'

Write-Host ''
Write-Host '完成。SnowLuma / AstrBot 两个窗口不要关。' -ForegroundColor Cyan
Write-Host '本窗口 8 秒后自动关闭。'
Start-Sleep -Seconds 8
