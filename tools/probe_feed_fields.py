"""只读探测 2：把说说原始字段全打出来，找点赞相关字段和详情里的评论列表。"""

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
    return t


def summarize(v):
    if isinstance(v, list):
        return f"list[{len(v)}]"
    if isinstance(v, dict):
        return f"dict{list(v.keys())[:6]}"
    s = str(v)
    return repr(s[:70])


async def main():
    ck = await get_cookies()
    c = SimpleCookie()
    c.load(ck)
    uin_raw = c["uin"].value if "uin" in c else "0"
    uin = uin_raw[1:] if uin_raw[:1].lower() == "o" else uin_raw
    gtk2 = gtk_from(c["p_skey"].value if "p_skey" in c else "")
    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        for extra in ({"need_comment": 1}, {"need_comment": 1, "need_like": 1}):
            p = {
                "g_tk": gtk2, "uin": uin, "ftype": 0, "sort": 0, "pos": 0,
                "num": 2, "replynum": 100, "format": "json",
                "need_private_comment": 1, **extra,
            }
            async with s.get(LIST, params=p, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=25)) as r:
                data = json.loads(strip_jsonp(await r.text()))
            msgs = data.get("msglist") or []
            print(f"\n########## 额外参数 {extra} → {len(msgs)} 条 ##########")
            if not msgs:
                continue
            m = msgs[0]
            print(f"tid={m.get('tid')}")
            for k in sorted(m):
                print(f"  {k} = {summarize(m.get(k))}")

        # 详情：全字段
        async with s.get(LIST, params={
                "g_tk": gtk2, "uin": uin, "ftype": 0, "sort": 0, "pos": 0,
                "num": 2, "replynum": 100, "format": "json", "need_comment": 1},
                headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            data = json.loads(strip_jsonp(await r.text()))
        tid = (data.get("msglist") or [{}])[0].get("tid")

        async with s.get(DETAIL, params={"uin": uin, "tid": tid, "format": "json",
                                         "g_tk": gtk2, "pos": 0, "num": 20},
                        headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            dj = json.loads(strip_jsonp(await r.text()))
        print(f"\n########## 详情 tid={tid} 全部字段 ##########")
        for k in sorted(dj):
            print(f"  {k} = {summarize(dj.get(k))}")
        print(f"\ncmtnum={dj.get('cmtnum')!r}")
        for key in ("commentlist", "conlist", "likemans", "like"):
            if key in dj:
                print(f"\n{key} 内容: {json.dumps(dj[key], ensure_ascii=False)[:600]}")


if __name__ == "__main__":
    asyncio.run(main())
