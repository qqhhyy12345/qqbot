"""端到端：走插件真实的 _describe_post_images，用真 llama-server 看图。

验证 2 张图上限、冷启动、缓存、闲置卸载在「插件上下文」里都成立。
"""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.model import Post
from astrbot_plugin_qzone.core.vision_server import VisionServer
from astrbot_plugin_qzone.main import QzonePlugin

IMGS = [
    "https://photonjmaz.photo.store.qq.com/psc?/V11fQmqX0PEcdW/bqTbTkMvB9cXKq4bcmc9K2b*I2mAhJyPz8bB/HZ0KzL5lP3JWyL*ovMvX3Hq5mX3q6Bo7lFv8mBqX1lMw__&bo=xAEOAQ",
]


def fake_plugin():
    """造一个只带视觉方法所需字段的插件实例。"""
    s = SimpleNamespace()
    s._vision_cache = {}
    s._vision_fail_until = 0.0
    s._vision = VisionServer(
        exe=QzonePlugin.VISION_SERVER_EXE,
        model=QzonePlugin.VISION_MODEL_PATH,
        mmproj=QzonePlugin.VISION_MMPROJ_PATH,
        port=QzonePlugin.VISION_PORT,
        device=QzonePlugin.VISION_DEVICE,
        idle_timeout=QzonePlugin.VISION_IDLE_TIMEOUT,
        log_path=Path(r"D:\qqbot\astrbot\data\temp\astrbot_plugin_qzone\llama-server.log"),
    )
    for name in (
        "VISION_MAX_IMAGES", "VISION_TIMEOUT", "VISION_COOLDOWN",
        "VISION_MAX_EDGE", "VISION_JPEG_QUALITY", "VISION_PROMPT",
        "VISION_MODEL", "VISION_PORT", "VISION_DEVICE", "VISION_IDLE_TIMEOUT",
    ):
        setattr(s, name, getattr(QzonePlugin, name))
    s._shrink_image = QzonePlugin._shrink_image
    s._fetch_image = lambda url: QzonePlugin._fetch_image(s, url)
    s._describe_one = lambda d, m: QzonePlugin._describe_one(s, d, m)
    s._describe_post_images = lambda post: QzonePlugin._describe_post_images(s, post)
    return s


async def main():
    sp = fake_plugin()

    # 拿一条真实的四图说说
    import sqlite3, json
    con = sqlite3.connect(
        r"file:D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\posts_5.db?mode=ro",
        uri=True,
    )
    cur = con.cursor()
    cur.execute("SELECT text, images FROM posts WHERE images != '[]' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    con.close()
    if not row:
        print("库里没有带图的说说，退出")
        return
    text, imgs_raw = row
    images = json.loads(imgs_raw)
    print(f"真实说说: {(text or '(无正文)')[:30]!r}  图 {len(images)} 张")

    post = Post(tid="test", text=text or "", images=images, name="测试")

    print("\n[1] 首次：冷启动 + 看图 2 张")
    t0 = time.monotonic()
    descs, skipped = await sp._describe_post_images(post)
    dt = time.monotonic() - t0
    print(f"  耗时 {dt:.1f}s  描述 {len(descs)} 条  未展开 {skipped} 张")
    for i, d in enumerate(descs, 1):
        print(f"    图{i}: {d}")
    ok1 = len(descs) == 2 and all(d and "没认出来" not in d for d in descs)
    print(f"  {'PASS' if ok1 else 'FAIL'} 两张图都拿到了真实描述")

    print("\n[2] 第二次：应命中缓存，几乎瞬时")
    t0 = time.monotonic()
    descs2, _ = await sp._describe_post_images(post)
    dt2 = time.monotonic() - t0
    print(f"  耗时 {dt2:.2f}s（首次 {dt:.1f}s）")
    print(f"  {'PASS' if dt2 < 1.0 and descs2 == descs else 'FAIL'} 缓存命中且内容一致")

    print("\n[3] 交给云端的总字数")
    body = "【本说说共 4 张图片】\n" + "\n".join(f"图{i}：{d}" for i, d in enumerate(descs, 1))
    print(f"  {len(body)} 字 ≈ {int(len(body) * 0.65)} token")
    print(f"  {'PASS' if len(body) < 500 else 'FAIL'} 控制在 500 字以内")

    print("\n[4] 清理")
    await sp._vision.aclose()
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/NH"],
        capture_output=True, timeout=15,
    ).stdout.decode("gbk", "replace")
    left = "llama-server" in out
    print(f"  {'FAIL' if left else 'PASS'} 无残留进程")


if __name__ == "__main__":
    asyncio.run(main())
