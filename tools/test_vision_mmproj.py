"""对比：视觉投影器放 GPU vs 放 CPU 的显存与耗时。

这决定「边玩游戏边看图」时到底占显卡多少。
"""

import asyncio
import base64
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

import aiohttp

from astrbot_plugin_qzone.core.vision_server import VisionServer
from astrbot_plugin_qzone.main import QzonePlugin

CACHE = Path(
    r"D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\cache\builtin_renderer"
)
PS = (
    "$c = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' "
    "-ErrorAction SilentlyContinue; "
    "if ($c) { $c.CounterSamples | ForEach-Object { "
    "'{0}|{1}' -f $_.InstanceName, [math]::Round($_.CookedValue/1MB,0) } }"
)


def gpu_mb() -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS], capture_output=True, timeout=40
    ).stdout.decode("utf-8", "replace")
    total = 0
    for line in out.splitlines():
        if "|" in line:
            try:
                total += int(float(line.split("|", 1)[1]))
            except Exception:
                pass
    return total


async def trial(mmproj_on_gpu: bool, img: Path) -> dict:
    label = "投影器在 GPU" if mmproj_on_gpu else "投影器在 CPU"
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    base = gpu_mb()

    vs = VisionServer(
        exe=QzonePlugin.VISION_SERVER_EXE,
        model=QzonePlugin.VISION_MODEL_PATH,
        mmproj=QzonePlugin.VISION_MMPROJ_PATH,
        port=QzonePlugin.VISION_PORT,
        device=QzonePlugin.VISION_DEVICE,
        mmproj_offload=mmproj_on_gpu,
        idle_timeout=20.0,
        log_path=Path(r"D:\qqbot\astrbot\data\temp\astrbot_plugin_qzone\llama-server.log"),
    )
    t0 = time.monotonic()
    ok = await vs.ensure()
    boot = time.monotonic() - t0
    if not ok:
        print("  拉起失败")
        return {"label": label, "ok": False}
    await asyncio.sleep(1.5)
    loaded = gpu_mb()

    # 推理
    raw = img.read_bytes()
    shrunk, mime = QzonePlugin._shrink_image(raw)
    uri = f"data:{mime};base64," + base64.b64encode(shrunk).decode()
    payload = {
        "model": "qwen2-vl-2b-instruct",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": QzonePlugin.VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}],
        "max_tokens": 160, "temperature": 0.2, "stream": False,
    }
    t1 = time.monotonic()
    desc = ""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120), trust_env=False
        ) as s:
            async with s.post(vs.chat_url, json=payload) as r:
                if r.status == 200:
                    d = await r.json()
                    desc = (d["choices"][0]["message"].get("content") or "").strip()
                else:
                    print(f"  HTTP {r.status}: {(await r.text())[:120]}")
    except Exception as exc:
        print(f"  推理异常 {type(exc).__name__}: {exc}")
    infer = time.monotonic() - t1

    await vs.aclose()
    await asyncio.sleep(2)
    after = gpu_mb()

    print(f"  冷启动      : {boot:.1f}s")
    print(f"  显存增量    : +{loaded - base} MB")
    print(f"  单图推理    : {infer:.1f}s")
    print(f"  描述        : {desc[:80]!r}")
    print(f"  卸载后残留  : {after - base:+d} MB")
    return {
        "label": label, "ok": True, "boot": boot,
        "vram": loaded - base, "infer": infer, "desc": desc,
        "resid": after - base,
    }


async def main():
    imgs = sorted(CACHE.glob("image_*.jpg"))
    if not imgs:
        print("没有测试图")
        return
    img = max(imgs, key=lambda p: p.stat().st_size)
    print(f"测试图: {img.name}  {img.stat().st_size:,}B")

    r1 = await trial(True, img)
    r2 = await trial(False, img)

    print(f"\n{'=' * 60}\n汇总\n{'=' * 60}")
    print(f"{'配置':<16}{'显存':>10}{'冷启动':>10}{'推理':>9}")
    for r in (r1, r2):
        if r.get("ok"):
            print(f"{r['label']:<16}{r['vram']:>8} MB{r['boot']:>9.1f}s{r['infer']:>8.1f}s")
    if r1.get("ok") and r2.get("ok"):
        print(f"\n放 CPU 省显存 {r1['vram'] - r2['vram']} MB，推理慢 {r2['infer'] - r1['infer']:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
