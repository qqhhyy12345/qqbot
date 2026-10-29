"""离线验证：llama-server 按需拉起 / 闲置自动卸载。

不碰 AstrBot、不碰 QQ，只测 VisionServer 这个进程管理器本身。
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

import aiohttp

from astrbot_plugin_qzone.core.vision_server import VisionServer
from astrbot_plugin_qzone.main import QzonePlugin

ROOT = Path(r"D:\qqbot\models\qwen2vl-2b")
EXE = Path(
    r"C:\Users\<user>\.lmstudio\extensions\backends"
    r"\llama.cpp-win-x86_64-vulkan-avx2-2.51.0\llama-server.exe"
)
MODEL = ROOT / "Qwen2-VL-2B-Instruct-Q4_K_M.gguf"
MMPROJ = ROOT / "mmproj-Qwen2-VL-2B-Instruct-f16.gguf"
CACHE = Path(
    r"D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\cache\builtin_renderer"
)
LOG = Path(r"D:\qqbot\_tmp_vision_test.log")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


async def port_alive(port=1234) -> bool:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=1.5), trust_env=False
        ) as s:
            async with s.get(f"http://127.0.0.1:{port}/health") as r:
                return r.status == 200
    except Exception:
        return False


def proc_of_server() -> list[int]:
    import subprocess

    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        timeout=15,
    ).stdout.decode("gbk", "replace")
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower().startswith("llama-server"):
            try:
                pids.append(int(parts[1]))
            except Exception:
                pass
    return pids


async def main():
    print("=" * 68)
    print("视觉服务托管测试  (闲置 20s 自动卸载)")
    print("=" * 68)

    for p in (EXE, MODEL, MMPROJ):
        check(f"文件存在 {p.name}", p.exists(), f"{p.stat().st_size:,}B" if p.exists() else "缺失")
    if len(FAIL):
        return

    imgs = sorted(CACHE.glob("image_*.jpg"))
    print(f"\n测试图: {len(imgs)} 张缓存图")

    print("\n[0] 测试前清场")
    if proc_of_server():
        print(f"  ! 已有 llama-server 进程 {proc_of_server()}，先杀掉")
        import subprocess
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc_of_server()[0])],
                       capture_output=True, timeout=15)
        await asyncio.sleep(1)
    check("端口 1234 起始无服务", not await port_alive())

    vs = VisionServer(
        exe=EXE, model=MODEL, mmproj=MMPROJ,
        port=1234, device="Vulkan0", idle_timeout=20.0,
        log_path=LOG,
    )

    print("\n[1] 首次 ensure()：冷启动")
    t0 = time.monotonic()
    ok = await vs.ensure()
    boot = time.monotonic() - t0
    check("拉起成功", ok, f"耗时 {boot:.1f}s")
    check("进程存在", bool(proc_of_server()), str(proc_of_server()))
    check("端口已监听", await port_alive())

    print("\n[2] 复用 ensure()：不应重复拉起")
    t0 = time.monotonic()
    ok = await vs.ensure()
    reuse = time.monotonic() - t0
    check("复用命中", ok and reuse < 2.0, f"{reuse:.2f}s")
    check("进程数仍为 1", len(proc_of_server()) == 1, str(proc_of_server()))

    print("\n[3] 真实推理")
    raw = imgs[0].read_bytes() if imgs else None
    if raw:
        shrunk, mime = QzonePlugin._shrink_image(raw)
        print(f"  原图 {len(raw):,}B -> {len(shrunk):,}B ({mime})")
        uri = f"data:{mime};base64," + base64.b64encode(shrunk).decode()
        payload = {
            "model": "qwen2-vl-2b-instruct",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": QzonePlugin.VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": uri}},
            ]}],
            "max_tokens": 160, "temperature": 0.2, "stream": False,
        }
        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=90), trust_env=False
            ) as s:
                async with s.post(vs.chat_url, json=payload) as r:
                    dt = time.monotonic() - t0
                    if r.status == 200:
                        d = await r.json()
                        txt = (d["choices"][0]["message"].get("content") or "").strip()
                        check("推理返回", bool(txt), f"{dt:.1f}s")
                        print(f"  描述: {txt[:120]!r}")
                    else:
                        check("推理返回", False, f"HTTP {r.status}: {(await r.text())[:150]}")
        except Exception as exc:
            check("推理返回", False, f"{type(exc).__name__}: {exc}")

    print("\n[4] 闲置 20s 后自动卸载（等 25s）")
    vs.touch()
    waited = 0
    gone_at = None
    while waited < 30:
        await asyncio.sleep(2)
        waited += 2
        if not proc_of_server() and not await port_alive():
            gone_at = waited
            break
    check("进程已退出", gone_at is not None, f"第 {gone_at}s 消失" if gone_at else "30s 内仍在")
    check("端口已释放", not await port_alive())

    print("\n[5] 二次调用：应重新拉起")
    t0 = time.monotonic()
    ok = await vs.ensure()
    dt = time.monotonic() - t0
    check("重新拉起", ok and bool(proc_of_server()), f"{dt:.1f}s")

    print("\n[6] aclose()：插件卸载时清理")
    await vs.aclose()
    await asyncio.sleep(1.5)
    check("aclose 后无残留进程", not proc_of_server(), str(proc_of_server()))
    check("aclose 后端口释放", not await port_alive())

    print("\n" + "=" * 68)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
