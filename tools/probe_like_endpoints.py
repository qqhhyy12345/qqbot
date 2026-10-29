"""只读探测 4：核对有评论的那条说说全部字段 + 试多种点赞列表接口。"""

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

LIKE_CANDIDATES = [
    f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/get_like_list_app",
    f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/getlike_list_app",
    f"https://h5.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/get_like_list_app",
    f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/get_like_list",
    f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_likelist_v6",
]


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
        async with s.get(LIST, params={
                "g_tk": gtk2, "uin": uin, "ftype": 0, "sort": 0, "pos": 0,
                "num": 5, "replynum": 100, "format": "json",
                "need_comment": 1, "need_private_comment": 1, "need_like": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            data = json.loads(strip_jsonp(await r.text()))
        msgs = data.get("msglist") or []

        target = None
        for m in msgs:
            if m.get("commentlist"):
                target = m
                break
        if not target:
            print("没找到带评论的说说")
            return
        tid = target.get("tid")
        print(f"带评论的说说 tid={tid} cmtnum={target.get('cmtnum')!r}")
        print("全部字段:")
        for k in sorted(target):
            v = target.get(k)
            if isinstance(v, (list, dict)):
                print(f"  {k} = {type(v).__name__}[{len(v)}]")
            else:
                print(f"  {k} = {str(v)[:60]!r}")

        print("\n===== 试点赞列表接口 =====")
        unikey = f"{BASE}/{uin}/mood/{tid}"
        for url in LIKE_CANDIDATES:
            for extra in ({"unikey": unikey, "format": "json", "g_tk": gtk2},
                          {"unikey": unikey, "format": "json", "g_tk": gtk2,
                           "opuin": uin, "fupdate": 1}):
                try:
                    async with s.get(url, params=extra, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=15)) as r:
                        raw = await r.text()
                    tag = "OK" if r.status == 200 and raw.strip() else "空"
                    print(f"  [{r.status} {tag}] {url.split('/')[-1]}  len={len(raw)}")
                    if raw.strip():
                        print(f"      {raw[:220]!r}")
                except Exception as exc:
                    print(f"  [异常] {url.split('/')[-1]}  {exc}")


if __name__ == "__main__":
    asyncio.run(main())
