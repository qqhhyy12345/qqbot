"""聊天图片本地转述 + 原图清扫：真实链路测试（真拉起 llama-server）。

清扫策略是「时间窗」：转述成功后延时扫 temp，只删生成超过 2 分钟的
media_image 文件。所以这里把测试图的 mtime 调老 300 秒来模拟"已经用完"。
"""

import asyncio
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from PIL import Image, ImageDraw

from astrbot_plugin_local_vision.core.vision_server import VisionServer
from astrbot_plugin_local_vision.main import LocalVisionPlugin

TEMP = Path(r"D:\qqbot\astrbot\data\temp")
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def make_img(path: Path, color: str, text: str) -> Path:
    im = Image.new("RGB", (420, 260), color)
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 400, 240], outline="white", width=4)
    d.text((40, 110), text, fill="white")
    im.save(path, "JPEG", quality=88)
    return path


def age(path: Path, seconds: float = 300) -> None:
    """把 mtime 往前拨，模拟"这张图是几分钟前收的"。"""
    ts = time.time() - seconds
    os.utime(path, (ts, ts))


def make_plugin():
    obj = object.__new__(LocalVisionPlugin)
    obj._cache = {}
    obj._lock = asyncio.Lock()
    obj._last_sweep_at = 0.0
    obj.SWEEP_MIN_AGE = 120.0
    obj.SWEEP_MIN_INTERVAL = 30.0
    obj.DELETE_LOCAL_COPY = True
    obj._vision = VisionServer(
        exe=LocalVisionPlugin.VISION_SERVER_EXE,
        model=LocalVisionPlugin.VISION_MODEL_PATH,
        mmproj=LocalVisionPlugin.VISION_MMPROJ_PATH,
        port=LocalVisionPlugin.VISION_PORT,
        device=LocalVisionPlugin.VISION_DEVICE,
        idle_timeout=LocalVisionPlugin.VISION_IDLE_TIMEOUT,
    )
    return obj


async def main():
    TEMP.mkdir(parents=True, exist_ok=True)
    img_a = make_img(TEMP / "media_image_TESTVIS_a.jpg", "#1f6f8b", "HELLO A")
    img_b = make_img(TEMP / "media_image_TESTVIS_b.jpg", "#8b1f5f", "SECOND B")

    p = make_plugin()

    print("[1] 冷启动 + 单图转述")
    t0 = time.monotonic()
    desc = await p._describe(str(img_a))
    cold = time.monotonic() - t0
    print(f"   {cold:.1f}s  描述={desc!r}")
    check("拿到描述", bool(desc), f"{len(desc or '')} 字")

    print("\n[2] 同一张再来（缓存）")
    t0 = time.monotonic()
    desc2 = await p._describe(str(img_a))
    warm = time.monotonic() - t0
    check("缓存命中", desc2 == desc and warm < 0.05, f"{warm*1000:.0f}ms")

    print("\n[3] 服务已热时换一张")
    t0 = time.monotonic()
    desc3 = await p._describe(str(img_b))
    hot = time.monotonic() - t0
    print(f"   {hot:.1f}s  描述={desc3!r}")
    check("热态远快于冷启", hot < cold, f"{hot:.1f}s vs {cold:.1f}s")

    print("\n[4] 模拟真实请求（历史图 + 本轮新图），顺带验证清扫")
    req = types.SimpleNamespace(
        contexts=[
            {"role": "user", "content": [
                {"type": "text", "text": "之前那张"},
                {"type": "image_url", "image_url": {"url": str(img_a)}},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "看到了"}]},
        ],
        image_urls=[str(img_b)],
        extra_user_content_parts=[],
    )
    age(img_a)  # 历史那张是几分钟前收的，属于"旧图"，该被清
    # img_b 是本轮刚收到的，mtime 保持新鲜，不能被误删
    await p._swap(req)
    # 这里不 sleep：清扫是同步的，_swap 返回时删除就该已经发生
    print(f"   image_urls={req.image_urls}")
    print(f"   parts={[getattr(x,'text',x) for x in req.extra_user_content_parts]}")
    print(f"   历史 part={req.contexts[0]['content'][1]}")
    check("本轮原图已清空", req.image_urls == [])
    check("注入文字描述", len(req.extra_user_content_parts) == 1)
    check("历史图片换成文字", req.contexts[0]["content"][1].get("type") == "text")
    check("旧图已被清扫（同步，无需等待）", not img_a.exists())
    check("本轮新图未被误删", img_b.exists())
    img_b.unlink(missing_ok=True)

    print("\n[5] 认不出来的图：保留原图、不删")
    bad = TEMP / "media_image_TESTVIS_bad.jpg"
    bad.write_bytes(b"not-an-image")
    req2 = types.SimpleNamespace(contexts=[], image_urls=[str(bad)], extra_user_content_parts=[])
    await p._swap(req2)
    check("坏图保留在 image_urls", req2.image_urls == [str(bad)])
    check("坏图不注入文字", req2.extra_user_content_parts == [])
    check("坏图文件没被删", bad.exists())
    bad.unlink(missing_ok=True)

    print("\n[6] 新鲜图不能被误删")
    fresh = make_img(TEMP / "media_image_TESTVIS_fresh.jpg", "#3f6f2b", "FRESH")
    await p._sweep_temp(force=True)
    check("刚收到的图不删", fresh.exists())
    fresh.unlink(missing_ok=True)

    await p._vision.aclose()
    print("\n" + "=" * 60)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f in FAIL:
        print(f"  失败: {f}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    asyncio.run(main())
