"""测 llama-server 运行时的显存占用（这对「边玩游戏边跑」是关键数据）。"""

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.vision_server import VisionServer
from astrbot_plugin_qzone.main import QzonePlugin

PS = """
$c = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue
if ($c) { $c.CounterSamples | ForEach-Object { '{0}|{1}' -f $_.InstanceName, [math]::Round($_.CookedValue/1MB,0) } }
"""


def gpu_mem() -> dict[str, int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS],
        capture_output=True, timeout=40,
    ).stdout.decode("utf-8", "replace")
    d = {}
    for line in out.splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            try:
                d[k.strip()] = int(float(v))
            except Exception:
                pass
    return d


async def main():
    print("=== 基线（模型没起）===")
    before = gpu_mem()
    for k, v in sorted(before.items()):
        if v > 50:
            print(f"  {k:34s} {v:6d} MB")

    vs = VisionServer(
        exe=QzonePlugin.VISION_SERVER_EXE,
        model=QzonePlugin.VISION_MODEL_PATH,
        mmproj=QzonePlugin.VISION_MMPROJ_PATH,
        port=QzonePlugin.VISION_PORT,
        device=QzonePlugin.VISION_DEVICE,
        idle_timeout=20.0,
        log_path=Path(r"D:\qqbot\astrbot\data\temp\astrbot_plugin_qzone\llama-server.log"),
    )
    print("\n=== 拉起 ===")
    if not await vs.ensure():
        print("  拉起失败")
        return
    await asyncio.sleep(2)

    print("=== 加载后 ===")
    after = gpu_mem()
    total = 0
    biggest = None
    for k, v in sorted(after.items()):
        delta = v - before.get(k, 0)
        if v > 50:
            mark = f"  (+{delta} MB)" if delta > 50 else ""
            print(f"  {k:34s} {v:6d} MB{mark}")
            if delta > total:
                total, biggest = delta, k
    print(f"\n  >>> 本次增加显存：约 {total} MB（{biggest}）")

    await vs.aclose()
    await asyncio.sleep(2)
    print("\n=== 卸载后 ===")
    final = gpu_mem()
    for k, v in sorted(final.items()):
        if v > 50:
            print(f"  {k:34s} {v:6d} MB")
    resid = sum(max(0, v - before.get(k, 0)) for k, v in final.items())
    print(f"\n  >>> 残留：约 {resid} MB（应接近 0）")


if __name__ == "__main__":
    asyncio.run(main())
