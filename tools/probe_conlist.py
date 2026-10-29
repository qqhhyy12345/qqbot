"""只读探测 3：看 conlist 到底是什么，以及点赞列表能不能单独拿到。"""

import asyncio
import json
import sys
from http.cookies import SimpleCookie

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
BASE = "https://user.qzone.qq.com"
LIST = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
DETAIL = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"
LIKE_LIST = f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/get_like_list_app"


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
    if t.startswith("_Callback("):
        t = t[t.index("(") + 1 : t.rindex(")")]
    if t.startswith("frameElement.callback("):
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
        async with s.get(LIST, params={
                "g_tk": gtk2, "uin": uin, "ftype": 0, "sort": 0, "pos": 0,
                "num": 3, "replynum": 100, "format": "json",
                "need_comment": 1, "need_private_comment": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            data = json.loads(strip_jsonp(await r.text()))
        msgs = data.get("msglist") or []

        for i, m in enumerate(msgs):
            print(f"\n===== 第{i+1}条 tid={m.get('tid')} cmtnum={m.get('cmtnum')!r} =====")
            for key in ("commentlist", "conlist"):
                v = m.get(key)
                if isinstance(v, list):
                    print(f"  {key}: {len(v)} 条")
                    for c0 in v[:3]:
                        print(f"    字段: {sorted(c0.keys())}")
                        print(f"    内容: {json.dumps(c0, ensure_ascii=False)[:500]}")

        # 点赞列表接口（只读 GET）
        tid = msgs[0].get("tid") if msgs else None
        if tid:
            unikey = f"{BASE}/{uin}/mood/{tid}"
            print(f"\n===== 点赞列表接口 unikey={unikey} =====")
            for params in (
                {"unikey": unikey, "format": "json", "g_tk": gtk2},
                {"unikey": unikey, "format": "json", "g_tk": gtk2, "fupdate": 1, "opuin": uin},
            ):
                async with s.get(LIKE_LIST, params=params, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=20)) as r:
                    raw = await r.text()
                print(f"  params={params} → HTTP {r.status} 长度 {len(raw)}")
                print(f"  原始前 400: {raw[:400]!r}")


if __name__ == "__main__":
    asyncio.run(main())
