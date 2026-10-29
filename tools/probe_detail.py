"""探测详情接口：评论能不能拿到、有没有点赞状态字段。全部只读。"""

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


def gtk(p: str) -> int:
    e = 5381
    for ch in p:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def clean(t: str) -> str:
    t = t.strip()
    if t.startswith("_Callback("):
        t = t[t.index("(") + 1 : t.rindex(")")]
    return t


async def main():
    ck = await get_cookies()
    c = SimpleCookie()
    c.load(ck)
    uin = c["uin"].value
    gtk2 = gtk(c["p_skey"].value)
    headers = {"Cookie": ck, "User-Agent": UA, "Referer": f"{BASE}/{uin}"}

    async with aiohttp.ClientSession(trust_env=False) as s:
        # 拉最近动态流（feeds3 含好友动态，评论更可能非空）
        url = (
            f"{BASE}/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
            f"?uin={uin}&scope=0&view=1&flag=1&filter=1&applist=all"
            f"&pagenum=1&count=10&g_tk={gtk2}&format=json&raw=1"
        )
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            body = clean(await r.text())
        try:
            d = json.loads(body)
            data = d.get("data") or d
            items = data.get("data") or []
            print(f"动态流条目: {len(items)}")
            # 找带评论的
            found = 0
            for it in items[:20]:
                if isinstance(it, dict) and it.get("commentlist"):
                    found += 1
                    print(f"  有条目带评论: {str(it.get('content'))[:40]!r}")
                    cl = it["commentlist"]
                    if isinstance(cl, list) and cl:
                        print(f"    评论数 {len(cl)}，字段: {sorted(cl[0].keys())}")
                        print(f"    首条: {str(cl[0].get('content'))[:60]!r} by {cl[0].get('name')}")
                    break
            if not found:
                print("  这批里没有带评论的条目")
        except Exception as exc:
            print(f"动态流解析失败: {exc}")
            print(f"  原始前 200: {body[:200]}")

        # 详情接口：拿自己一条说说看全字段
        url2 = (
            f"{LIST}?uin={uin}&ftype=0&sort=0&pos=0&num=1&replynum=0"
            f"&g_tk={gtk2}&code=2&format=json&need_private_comment=1"
        )
        async with s.get(url2, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            d2 = json.loads(clean(await r.text()))
        msgs = d2.get("msglist") or []
        if not msgs:
            print("\n没有自己的说说，跳过详情探测")
            return
        tid = msgs[0]["tid"]
        print(f"\n=== 详情接口探测（tid={tid}）===")
        url3 = f"{DETAIL}?uin={uin}&tid={tid}&format=jsonp&g_tk={gtk2}"
        async with s.get(url3, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            body3 = clean(await r.text())
        try:
            j = json.loads(body3)
            keys = sorted(j.keys())
            print("详情返回字段：")
            print("  " + ", ".join(keys))
            print("\n「赞」相关：")
            hit = False
            for k in keys:
                if any(w in k.lower() for w in ("like", "praise", "digg", "praise")):
                    print(f"  {k} = {json.dumps(j.get(k), ensure_ascii=False)[:120]}")
                    hit = True
            if not hit:
                print("  （无任何点赞相关字段）")
            print("\n「评论」相关：")
            for k in keys:
                if "comment" in k.lower():
                    v = j.get(k)
                    if isinstance(v, list):
                        print(f"  {k}: {len(v)} 条")
                    else:
                        print(f"  {k} = {str(v)[:60]!r}")
        except Exception as exc:
            print(f"详情解析失败: {exc}")
            print(f"  原始前 300: {body3[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
