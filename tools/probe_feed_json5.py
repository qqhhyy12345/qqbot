"""只读探测 6：用 json5 解析动态流，找 feed 列表在哪、赞信息怎么取。"""

import asyncio
import json
import re
import sys
from http.cookies import SimpleCookie

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
import json5

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

    start, end = raw.find("{"), raw.rfind("}")
    j = json5.loads(raw[start : end + 1].replace("undefined", "null"))
    print(f"顶层键: {sorted(j.keys())}")
    data = j.get("data")
    print(f"data 类型: {type(data).__name__}  键: {sorted(data.keys()) if isinstance(data, dict) else ''}")

    inner = data.get("data") if isinstance(data, dict) else None
    print(f"\ndata['data'] 类型: {type(inner).__name__}")
    if isinstance(inner, str):
        # 可能是再套一层的 JS 对象字符串
        inner2 = json5.loads(inner.replace("undefined", "null"))
        print(f"  二次解析后类型: {type(inner2).__name__} 键: {sorted(inner2.keys())[:15]}")
        inner = inner2
    if isinstance(inner, dict):
        for k, v in inner.items():
            print(f"  {k}: {type(v).__name__}"
                  + (f"[{len(v)}]" if isinstance(v, (list, dict)) else f" = {str(v)[:40]!r}"))
    if isinstance(inner, list):
        print(f"  列表长度 {len(inner)}")
        for idx, it in enumerate(inner[:4]):
            print(f"\n=== 第{idx+1}项字段: {sorted(it.keys()) if isinstance(it, dict) else type(it).__name__} ===")
            if not isinstance(it, dict):
                continue
            print(f"  appid={it.get('appid')!r} key={it.get('key')!r} abstime={it.get('abstime')!r}")
            html = str(it.get("html") or "")
            print(f"  html 长度 {len(html)}")
            for pat in (r'data-likecnt="(\d+)"', r'data-islike="(\d+)"',
                        r'data-unikey="([^"]+)"'):
                m = re.search(pat, html)
                print(f"    {pat.split('\"')[0][5:]} = {m.group(1) if m else None}")
            i = html.find("f-like-cnt")
            if i > 0:
                print(f"    赞区块: {html[max(0,i-420):i+70]}")


if __name__ == "__main__":
    asyncio.run(main())
