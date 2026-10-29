"""离线验证 _build_post_images：用真实空间图片走一遍插件的新代码。

不打 AstrBot 的事件、不联网调模型，只验：
  1) 能不能拿到 ImageContent
  2) base64 解出来是不是合法图片
  3) mime 判断对不对
  4) 张数上限是否生效
  5) 坏 URL 是否只跳过、不抛错
"""

import asyncio
import base64
import io
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.model import Post  # noqa: E402
from astrbot_plugin_qzone.core.post import Post as PostAlt  # noqa: E402

from astrbot_plugin_qzone.main import QzonePlugin  # noqa: E402

DB = r"D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\posts_5.db"
TEMP = Path(r"D:\qqbot\astrbot\data\temp\qzone_img_test")
TEMP.mkdir(parents=True, exist_ok=True)


def load_posts() -> list[Post]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT id, tid, uin, name, gin, text, images, videos, anon, status, create_time, rt_con, comments, extra_text FROM posts ORDER BY id")
    rows = cur.fetchall()
    con.close()
    posts = []
    for r in rows:
        posts.append(
            PostAlt(
                id=r[0], tid=r[1], uin=r[2], name=r[3], gin=r[4], text=r[5],
                images=__import__("json").loads(r[6]),
                videos=__import__("json").loads(r[7]),
                anon=bool(r[8]), status=r[9], create_time=r[10],
                rt_con=r[11],
                comments=[], extra_text=r[13],
            )
        )
    return posts


class FakeCtx:
    def get_config(self):
        return {"provider_settings": {"image_compress_options": {"max_size": 1280}}}


def fake_self(**over):
    s = SimpleNamespace(
        context=FakeCtx(),
        cfg=SimpleNamespace(temp_dir=TEMP),
        POST_IMAGE_LIMIT=4,
    )
    # 把类里其它方法绑到假对象上，否则未绑定调用会找不到
    s._image_max_edge = lambda: QzonePlugin._image_max_edge(s)
    s._sniff_image_mime = QzonePlugin._sniff_image_mime
    s._shrink_image = QzonePlugin._shrink_image
    s._build_post_images = lambda post: QzonePlugin._build_post_images(s, post)
    for k, v in over.items():
        setattr(s, k, v)
    return s


async def run_case(label, post, me):
    print(f"\n=== {label}（原图 {len(post.images)} 张）===")
    blocks, skipped = await QzonePlugin._build_post_images(me, post)
    print(f"  产出 {len(blocks)} 块, 跳过 {skipped} 张")
    for i, b in enumerate(blocks):
        raw = base64.b64decode(b.data)
        try:
            from PIL import Image

            im = Image.open(io.BytesIO(raw))
            print(f"    [{i}] {b.mimeType:10s} {len(raw):>9,}B  {im.format} {im.size}")
        except Exception as exc:
            print(f"    [{i}] {b.mimeType:10s} {len(raw):>9,}B  不是合法图片: {exc}")
    return blocks, skipped


async def main():
    posts = load_posts()
    me = fake_self()

    for p in posts:
        if p.images:
            await run_case(f"post id={p.id}", p, me)

    print("\n=== 边界：上限 2 张 ===")
    p3 = next(p for p in posts if len(p.images) >= 4)
    await run_case("limit=2", p3, fake_self(POST_IMAGE_LIMIT=2))

    print("\n=== 边界：混入坏 URL ===")
    bad = PostAlt(uin=1, name="x", text="测试", images=["http://127.0.0.1:9/nope.jpg"])
    await run_case("坏URL", bad, me)

    print("\n=== 边界：无图片 ===")
    empty = PostAlt(uin=1, name="x", text="只有文字", images=[])
    b, s = await QzonePlugin._build_post_images(me, empty)
    print(f"  产出 {len(b)} 块, 跳过 {s} 张  (应为 0/0)")

    print("\n=== 返回结构检查 ===")
    import mcp.types as mt

    blocks, _ = await QzonePlugin._build_post_images(me, p3)
    res = mt.CallToolResult(
        content=[mt.TextContent(type="text", text="正文"), *blocks]
    )
    print(f"  CallToolResult 块数={len(res.content)}")
    print(f"  首块类型={type(res.content[0]).__name__}")
    print(f"  其余类型={[type(c).__name__ for c in res.content[1:]]}")
    print(f"  序列化长度={len(res.model_dump_json()):,} 字符")


if __name__ == "__main__":
    asyncio.run(main())
