"""真实链路：动态流 → parse_like_map → _format_likes → 她看到的文本。

同时验证评论渲染没被改坏。
"""

import asyncio
import json
import sys
from http.cookies import SimpleCookie
from types import SimpleNamespace

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

import aiohttp
import json5

from astrbot_plugin_qzone.core.qzone.parser import QzoneParser
from astrbot_plugin_qzone.main import QzonePlugin

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
BASE = "https://user.qzone.qq.com"
ZONE_LIST = f"{BASE}/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
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


async def cookies():
    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.post("http://127.0.0.1:3000/get_cookies",
                          json={"domain": "user.qzone.qq.com"},
                          timeout=aiohttp.ClientTimeout(total=15)) as r:
            d = await r.json(content_type=None)
    return d["data"]["cookies"]


async def main():
    print("=" * 68)
    print("点赞链路：动态流 → parse_like_map → _format_likes")
    print("=" * 68)

    ck = await cookies()
    c = SimpleCookie()
    c.load(ck)
    uin_raw = c["uin"].value if "uin" in c else "0"
    uin = uin_raw[1:] if uin_raw[:1].lower() == "o" else uin_raw
    g2 = gtk(c["p_skey"].value if "p_skey" in c else "")
    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        # 1) 动态流
        async with s.get(ZONE_LIST, params={
                "uin": uin, "scope": 0, "view": 1, "filter": "all", "flag": 1,
                "applist": "all", "pagenum": 1, "aisortEndTime": 0,
                "aisortOffset": 0, "aisortBeginTime": 0, "begintime": 0,
                "format": "json", "g_tk": g2, "useutf8": 1,
                "outputhtmlfeed": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            raw = await r.text()
        st, en = raw.find("{"), raw.rfind("}")
        feed_data = json5.loads(raw[st:en + 1].replace("undefined", "null"))

        like_map = QzoneParser.parse_like_map(feed_data)
        check("动态流解析出点赞条目", len(like_map) > 0, f"{len(like_map)} 条有赞")
        print(f"  like_map = {json.dumps(like_map, ensure_ascii=False)}")

        # 2) 渲染
        stub = SimpleNamespace(VIEW_LIKE_NAME_LIMIT=6)
        rendered = {tid: QzonePlugin._format_likes(stub, info) for tid, info in like_map.items()}
        for tid, txt in rendered.items():
            print(f"  tid={tid} →{json.dumps(txt, ensure_ascii=False)}")
        check("渲染出「点赞 N 人」", any("【点赞" in t for t in rendered.values()))

        tid0 = next(iter(like_map.keys()), None)
        if tid0 and like_map[tid0]["names"]:
            check("带出了点赞人昵称",
                  like_map[tid0]["names"][0] in rendered[tid0],
                  f"首名={like_map[tid0]['names'][0]!r}")

        # 3) 空值不炸
        check("空信息返回空串", QzonePlugin._format_likes(stub, None) == "")
        check("零赞返回空串", QzonePlugin._format_likes(stub, {"count": 0, "names": []}) == "")

        # 4) 评论渲染回归
        async with s.get(LIST, params={
                "g_tk": g2, "uin": uin, "ftype": 0, "sort": 0, "pos": 0,
                "num": 10, "replynum": 100, "callback": "_preloadCallback",
                "code_version": 1, "format": "json", "need_comment": 1,
                "need_private_comment": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            body = (await r.text()).strip()
        if body.startswith("_preloadCallback("):
            body = body[body.index("(") + 1: body.rindex(")")]
        msgs = json.loads(body).get("msglist") or []
        posts = QzoneParser.parse_feeds(msgs)
        with_c = [p for p in posts if p.comments]
        check("仍能解析出带评论的说说", len(with_c) > 0, f"{len(with_c)} 条")
        if with_c:
            txt = QzonePlugin._format_comments(stub.__class__(VIEW_COMMENT_LIMIT=10), with_c[0])
            check("评论渲染含「评论区」", "【评论区" in txt, txt.replace("\n", " / "))

    print("\n" + "=" * 68)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f in FAIL:
        print(f"  失败: {f}")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
