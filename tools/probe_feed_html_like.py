"""只读探测 5：好友动态流（feeds3_html_more）里有没有赞。

只发 GET，不点赞。
"""

import asyncio
import json
import re
import sys
from http.cookies import SimpleCookie

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
BASE = "https://user.qzone.qq.com"
ZONE_LIST = f"{BASE}/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"


async def get_cookies():
    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.post(
            "http://127.0.0.1:3000/get_cookies",
            json={"domain": "user.qzone.qq.com"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            d = await r.json(content_type=None)
    return d["data"]["cookies"]


def gtk_from(p_skey: str) -> int:
    e = 5381
    for ch in p_skey:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def strip_jsonp(text: str) -> str:
    t = text.strip()
    for pfx in ("_Callback(", "frameElement.callback("):
        if t.startswith(pfx):
            t = t[t.index("(") + 1 : t.rindex(")")]
    return t


async def main():
    ck = await get_cookies()
    c = SimpleCookie()
    c.load(ck)
    uin_raw = c["uin"].value if "uin" in c else "0"
    uin = uin_raw[1:] if uin_raw[:1].lower() == "o" else uin_raw
    gtk2 = gtk_from(c["p_skey"].value if "p_skey" in c else "")
    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        async with s.get(ZONE_LIST, params={
                "uin": uin, "scope": 0, "view": 1, "filter": "all", "flag": 1,
                "applist": "all", "pagenum": 1, "aisortEndTime": 0,
                "aisortOffset": 0, "aisortBeginTime": 0, "begintime": 0,
                "format": "json", "g_tk": gtk2, "useutf8": 1,
                "outputhtmlfeed": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            raw = await r.text()
        print(f"HTTP {r.status} 长度 {len(raw)}")
        try:
            j = json.loads(strip_jsonp(raw))
        except Exception as exc:
            # 这个接口返回的是 JS 对象字面量（单引号），不是严格 JSON，
            # 直接在原文上做关键词统计即可。
            print(f"JSON 失败（预期内）: {exc}")
            for kw in ("赞", "like", "praise", "digg", "unikey", "cmtnum", "likemans"):
                hits = len(re.findall(kw, raw, re.I))
                print(f"  关键词 {kw!r}: {hits} 次")
            for kw in ("赞", "unikey", "likemans"):
                i = raw.find(kw)
                if i >= 0:
                    print(f"\n--- {kw} 附近 ---\n{raw[max(0,i-250):i+250]}")
            return
        print(f"顶层键: {sorted(j.keys())}")
        data = j.get("data") or []
        print(f"data 条数: {len(data) if isinstance(data, list) else type(data).__name__}")
        if isinstance(data, list) and data:
            html = "\n".join(
                str(item.get("html") or item.get("data") or "") for item in data
            )
            print(f"html 拼接长度 {len(html)}")
            for kw in ("赞", "like", "praise", "digg", "unikey", "cmtnum"):
                hits = len(re.findall(kw, html, re.I))
                print(f"  关键词 {kw!r}: {hits} 次")
            idx = html.find("赞")
            if idx >= 0:
                print(f"\n--- 赞 附近片段 ---\n{html[max(0,idx-300):idx+300]}")
            idx2 = html.lower().find("unikey")
            if idx2 >= 0:
                print(f"\n--- unikey 附近 ---\n{html[max(0,idx2-200):idx2+300]}")


if __name__ == "__main__":
    asyncio.run(main())
