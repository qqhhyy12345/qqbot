"""只读探测：点赞接口返回什么、说说数据里有没有点赞状态。

点赞部分故意用不存在的 tid —— 不会真赞到任何人，只看响应结构。
"""

import asyncio
import hashlib
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
DOLIKE = f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
LIST = (
    f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
)


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
    uin = c["uin"].value if "uin" in c else ""
    p_skey = c["p_skey"].value if "p_skey" in c else ""
    gtk2 = gtk_from(p_skey)
    print(f"uin={uin}  p_skey 长度={len(p_skey)}  g_tk={gtk2}")

    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        # ---------- 1. 拉一条说说，看原始字段 ----------
        url = (
            f"{LIST}?uin={uin}&ftype=0&sort=0&pos=0&num=5&replynum=0"
            f"&g_tk={gtk2}&code=2&format=json&need_private_comment=1"
        )
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = strip_jsonp(await r.text())
        data = json.loads(body)
        msgs = data.get("msglist") or []
        print(f"\n=== 拉到 {len(msgs)} 条说说 ===")
        if msgs:
            m = msgs[0]
            keys = sorted(m.keys())
            print("单条说说全部字段：")
            print("  " + ", ".join(keys))
            print("\n与「赞」相关的字段：")
            for k in keys:
                if any(w in k.lower() for w in ("like", "praise", "praise", "digg")):
                    print(f"  {k} = {m.get(k)!r}")
            print("\n评论相关字段：")
            for k in keys:
                if "comment" in k.lower():
                    v = m.get(k)
                    if isinstance(v, list):
                        print(f"  {k}: {len(v)} 条")
                        if v:
                            print(f"    首条字段: {sorted(v[0].keys())}")
                            print(f"    首条内容: {str(v[0].get('content'))[:60]!r}")
                    else:
                        print(f"  {k} = {str(v)[:60]!r}")

            # ---------- 2. 探测点赞接口结构（假 tid，不会真赞） ----------
            print("\n=== 点赞接口响应结构（用不存在的 tid 探测）===")
            fake_tid = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            form = {
                "qzreferrer": f"{BASE}/{uin}",
                "opuin": uin,
                "unikey": f"{BASE}/{uin}/mood/{fake_tid}",
                "curkey": f"{BASE}/{uin}/mood/{fake_tid}",
                "appid": 311,
                "from": 1,
                "typeid": 0,
                "abstime": 1792738000,
                "fid": fake_tid,
                "active": 0,
                "format": "json",
                "fupdate": 1,
            }
            async with s.post(
                DOLIKE,
                params={"g_tk": gtk2},
                data=form,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                raw = await r.text()
                print(f"HTTP {r.status}  原始长度 {len(raw)}")
            print(f"原始响应: {raw[:400]}")
            try:
                j = json.loads(strip_jsonp(raw))
                print(f"解析后顶层键: {sorted(j.keys())}")
                print(f"  code = {j.get('code')!r}  (type={type(j.get('code')).__name__})")
                print(f"  message = {str(j.get('message'))[:80]!r}")
                print(f"  subcode = {j.get('subcode')!r}")
                print("\n  >>> 插件判断成功用的是 code == 0；这条是假 tid，code 应为非 0")
            except Exception as exc:
                print(f"解析失败: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
