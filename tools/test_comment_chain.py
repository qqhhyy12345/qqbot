"""真实数据链路：接口 → parser → Post.comments → 渲染文本。

证明评论不是"理论上能拿到"，而是真的能走到她眼前。
"""

import asyncio
import json
import sys
from http.cookies import SimpleCookie
from types import SimpleNamespace

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

import aiohttp

from astrbot_plugin_qzone.core.qzone.parser import QzoneParser
from astrbot_plugin_qzone.main import QzonePlugin

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
BASE = "https://user.qzone.qq.com"
LIST = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def gtk(p):
    e = 5381
    for ch in p:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def clean(t):
    t = t.strip()
    return t[t.index("(") + 1 : t.rindex(")")] if t.startswith("_Callback(") else t


async def main():
    print("=" * 66)
    print("真实链路：接口 → 解析 → 她的视野")
    print("=" * 66)

    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.post(
            "http://127.0.0.1:3000/get_cookies",
            json={"domain": "user.qzone.qq.com"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            ck = (await r.json(content_type=None))["data"]["cookies"]
        c = SimpleCookie()
        c.load(ck)
        me = "".join(ch for ch in c["uin"].value if ch.isdigit())
        g2 = gtk(c["p_skey"].value)
        url = (
            f"{LIST}?uin={me}&ftype=0&sort=0&pos=0&num=10&replynum=100"
            f"&g_tk={g2}&code=2&format=json&need_comment=1&need_private_comment=1"
        )
        async with s.get(
            url,
            headers={"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{me}"},
            timeout=aiohttp.ClientTimeout(total=25),
        ) as r:
            raw = json.loads(clean(await r.text()))

    msgs = raw.get("msglist") or []
    print(f"\n拉到 {len(msgs)} 条说说")

    posts = QzoneParser.parse_feeds(msgs)
    print(f"解析出 {len(posts)} 个 Post")
    check("解析条数一致", len(posts) == len(msgs), f"{len(posts)} vs {len(msgs)}")

    with_cmt = [p for p in posts if p.comments]
    print(f"带评论的说说: {len(with_cmt)} 条")

    if with_cmt:
        p = with_cmt[0]
        print(f"\n--- 样例：{p.tid[:16]}... 共 {len(p.comments)} 条评论")
        for cm in p.comments[:5]:
            kind = "楼中楼" if cm.parent_tid else "主楼"
            print(f"    [{kind}] {cm.nickname}: {cm.content[:40]!r}")
        check("评论被解析进 Post", True, f"{len(p.comments)} 条")

        fake = SimpleNamespace(VIEW_COMMENT_LIMIT=QzonePlugin.VIEW_COMMENT_LIMIT)
        block = QzonePlugin._format_comments(fake, p)
        print("\n--- 她实际读到的那一段：")
        for line in block.splitlines():
            print("   ", line)
        check("渲染非空", bool(block.strip()))
        check("含昵称与内容", p.comments[0].nickname in block)
    else:
        print("  （最近的说说都没有评论，用刚才已验证的单条数据）")

    # 兜底：用已知有评论的那条手工验证
    print("\n--- 已知有评论的那条（tid=8f3e55aaf5b1）")
    hit = next((m for m in msgs if m.get("tid", "").startswith("8f3e55aaf5b1")), None)
    if hit:
        one = QzoneParser.parse_feeds([hit])
        if one:
            n = len(one[0].comments)
            print(f"    解析出 {n} 条评论")
            fake = SimpleNamespace(VIEW_COMMENT_LIMIT=QzonePlugin.VIEW_COMMENT_LIMIT)
            print("   ", QzonePlugin._format_comments(fake, one[0]).replace("\n", "\n    "))
            check("这条的评论能被解析", n >= 1, f"{n} 条")
    else:
        print("    这条不在最新 10 条里")

    print("\n" + "=" * 66)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败: " + ", ".join(FAIL))
    print("=" * 66)


if __name__ == "__main__":
    asyncio.run(main())
