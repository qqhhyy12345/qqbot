"""只读探测：用插件同款参数拉自己第一条说说，看评论/点赞字段到底有没有。

不写任何东西，只发 GET。
"""

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


async def main():
    ck = await get_cookies()
    c = SimpleCookie()
    c.load(ck)
    uin_raw = c["uin"].value if "uin" in c else "0"
    uin = uin_raw[1:] if uin_raw[:1].lower() == "o" else uin_raw
    p_skey = c["p_skey"].value if "p_skey" in c else ""
    gtk2 = gtk_from(p_skey)
    print(f"登录 uin={uin}  g_tk={gtk2}")

    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        # 插件同款参数
        params = {
            "g_tk": gtk2,
            "uin": uin,
            "ftype": 0,
            "sort": 0,
            "pos": 0,
            "num": 3,
            "replynum": 100,
            "callback": "_preloadCallback",
            "code_version": 1,
            "format": "json",
            "need_comment": 1,
            "need_private_comment": 1,
        }
        async with s.get(
            LIST, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
        ) as r:
            body = strip_jsonp(await r.text())
        data = json.loads(body)
        print(f"\n=== 列表接口 code={data.get('code')} message={data.get('message')!r} ===")
        msgs = data.get("msglist") or []
        print(f"拿到 {len(msgs)} 条说说")

        for i, m in enumerate(msgs):
            tid = m.get("tid")
            txt = (m.get("content") or "").replace("\n", " ")[:40]
            cl = m.get("commentlist")
            print(f"\n--- 第{i+1}条 tid={tid} 内容={txt!r} ---")
            print(f"  与评论相关字段: {[k for k in m if 'comment' in k.lower()]}")
            print(f"  commentlist 类型={type(cl).__name__} 条数={len(cl) if isinstance(cl, list) else 'N/A'}")
            if isinstance(cl, list) and cl:
                print(f"    首条字段: {sorted(cl[0].keys())}")
                print(f"    首条: {str(cl[0].get('content'))[:60]!r} by {cl[0].get('name')!r}")
            print(f"  与赞相关字段: {[k for k in m if any(w in k.lower() for w in ('like','praise','digg','unikey'))]}")
            for k in m:
                if any(w in k.lower() for w in ("likemans", "likes", "praiseman", "unikey")):
                    print(f"    {k} = {str(m.get(k))[:120]!r}")

        # 再试详情接口
        if msgs:
            tid = msgs[0].get("tid")
            print(f"\n=== 详情接口 tid={tid} ===")
            async with s.get(
                DETAIL,
                params={"uin": uin, "tid": tid, "format": "json", "g_tk": gtk2},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=25),
            ) as r:
                dtext = strip_jsonp(await r.text())
            try:
                dj = json.loads(dtext)
                print(f"  code={dj.get('code')} message={dj.get('message')!r}")
                dcl = dj.get("commentlist")
                print(f"  commentlist 条数={len(dcl) if isinstance(dcl, list) else 'N/A'}")
                print(f"  顶层键: {sorted(dj.keys())[:20]}")
            except Exception as exc:
                print(f"  解析失败: {exc}  原始前 200: {dtext[:200]!r}")


if __name__ == "__main__":
    asyncio.run(main())
