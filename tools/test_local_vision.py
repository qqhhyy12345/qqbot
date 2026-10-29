"""离线验证本地视觉链路（不依赖 LM Studio）。

覆盖：
  1) _shrink_image      缩图是否生效
  2) _fetch_image       真实 QQ 图片能否下载（关键：必须绕过本机全局代理）
  3) _describe_post_images 完整流程（模型用假实现顶替）
  4) 失败降级           模型不可用时是否只占位、不抛错
  5) 失败冷却           连续调用不应每次都白等超时
  6) 描述缓存           同一 URL 只算一次
  7) 文本拼装           最终给模型看的文字长什么样
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.post import Post as PostCls  # noqa: E402
from astrbot_plugin_qzone.main import QzonePlugin  # noqa: E402

DB = r"D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\posts_5.db"


def load_posts():
    import sqlite3

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute(
        "SELECT id, tid, uin, name, gin, text, images, videos, anon, status,"
        " create_time, rt_con, comments, extra_text FROM posts ORDER BY id"
    )
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        out.append(
            PostCls(
                id=r[0], tid=r[1], uin=r[2], name=r[3], gin=r[4], text=r[5],
                images=json.loads(r[6]), videos=json.loads(r[7]), anon=bool(r[8]),
                status=r[9], create_time=r[10], rt_con=r[11], comments=[],
                extra_text=r[13],
            )
        )
    return out


def bind(me):
    """把类方法绑到假对象上。"""
    me._shrink_image = QzonePlugin._shrink_image
    me._fetch_image = lambda url: QzonePlugin._fetch_image(me, url)
    me._describe_one = me._describe_one_impl
    me._describe_post_images = lambda p: QzonePlugin._describe_post_images(me, p)
    return me


def make(desc_impl):
    me = SimpleNamespace(
        VISION_MAX_IMAGES=2,
        VISION_TIMEOUT=3.0,
        VISION_COOLDOWN=120.0,
        _vision_cache={},
        _vision_fail_until=0.0,
        _describe_one_impl=desc_impl,
    )
    return bind(me)


async def main():
    posts = load_posts()
    with_img = [p for p in posts if p.images]
    print(f"有图说说 {len(with_img)} 条\n")

    # ---- 1) 缩图 ----
    print("[1] _shrink_image")
    for p in with_img:
        raw = await QzonePlugin._fetch_image(make(None), p.images[0])
        if not raw:
            print("    下载失败，跳过")
            continue
        small, mime = QzonePlugin._shrink_image(raw)
        print(f"    {len(raw):>9,}B -> {len(small):>8,}B  {mime}  压缩 {100*(1-len(small)/len(raw)):.0f}%")
        break

    # ---- 2) 真实下载（验证代理绕过）----
    print("\n[2] _fetch_image 真实下载")
    me = make(None)
    ok = 0
    for p in with_img[:3]:
        raw = await me._fetch_image(p.images[0])
        print(f"    {'OK' if raw else '失败'}  {len(raw) if raw else 0:>9,}B  {p.images[0][:60]}")
        if raw:
            ok += 1
    print(f"    -> {ok}/3 成功" + ("（代理绕过有效）" if ok else "（代理仍未绕过！）"))

    # ---- 3) 完整流程（假模型）----
    print("\n[3] _describe_post_images（假模型返回固定描述）")
    async def fake_desc(data, mime):
        await asyncio.sleep(0.05)
        return f"一张示例描述（{len(data)} 字节）"

    me = make(fake_desc)
    p = next(x for x in with_img if len(x.images) >= 4)
    descs, skipped = await me._describe_post_images(p)
    print(f"    原图 {len(p.images)} 张 -> 描述 {len(descs)} 条, 未展开 {skipped}")
    for i, d in enumerate(descs, 1):
        print(f"      图{i}：{d}")

    # ---- 4) 失败降级 ----
    print("\n[4] 模型不可用时的降级")
    async def dead_desc(data, mime):
        return None

    me = make(dead_desc)
    t0 = time.monotonic()
    descs, skipped = await me._describe_post_images(p)
    cost = time.monotonic() - t0
    print(f"    描述 {len(descs)} 条, 未展开 {skipped}, 耗时 {cost:.2f}s")
    print(f"    首条内容: {descs[0] if descs else '(无)'}")
    print(f"    是否进入冷却: {'是' if me._vision_fail_until > 0 else '否'}")

    # ---- 5) 冷却生效 ----
    t0 = time.monotonic()
    await me._describe_post_images(p)
    cost2 = time.monotonic() - t0
    print(f"    冷却后再调用耗时 {cost2:.4f}s（应远小于第一次，说明没重复等超时）")

    # ---- 6) 缓存 ----
    print("\n[6] 描述缓存")
    calls = {"n": 0}
    async def counting_desc(data, mime):
        calls["n"] += 1
        return "缓存测试描述"

    me = make(counting_desc)
    await me._describe_post_images(p)
    first = calls["n"]
    await me._describe_post_images(p)
    second = calls["n"]
    print(f"    第一次调用模型 {first} 次，第二次累计 {second} 次（应相同=命中缓存）")

    # ---- 7) 最终文本长什么样 ----
    print("\n[7] 交给云端的文字（拼装结果）")
    me = make(fake_desc)
    descs, skipped = await me._describe_post_images(p)
    lines = [x for x in ("", p.text) if x]
    text = "\n".join(lines).strip() or "(这条说说没有文字内容)"
    text += f"\n【本说说共 {len(p.images)} 张图片】"
    for i, d in enumerate(descs, 1):
        text += f"\n图{i}：{d}"
    if skipped:
        text += f"\n（另有 {skipped} 张未展开）"
    print("    " + text.replace("\n", "\n    "))
    print(f"\n    合计 {len(text)} 字（约 {int(len(text)*0.7)} token）")


if __name__ == "__main__":
    asyncio.run(main())
