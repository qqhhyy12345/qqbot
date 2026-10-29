"""第七轮：百度百科直达的所有可能入口。

已知：
  - baike.baidu.com/item/* 直接 403（桌面/移动/Referer 全试过）
  - 必应 RSS 能拿到百度百科的**摘要**，说明必应索引了百科
  - 核心矛盾：必应只认真检索第一个 token → 中文专名必被单字化

要测的入口（决定这方案生死）：
  A. cookie 预热后再请求 item
  B. 百科站内搜索接口（比通用搜索风控松？）
  C. wapbaike 移动独立域名
  D. 必应 site: 限定 baike.baidu.com（复测，之前测过但值得确认）
  E. 搜狗百科快照
  F. 百度百科开放 API（老 appid）
"""
import asyncio
import json
import re
import sys
from urllib.parse import quote, quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MUA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
TERM = "珂莱塔"   # 必应必单字化的生僻专名
TERM2 = "崩坏星穹铁道"


async def get(session, url, label, headers=None, show=300, want_baike=False):
    try:
        async with session.get(url, headers=headers or {}, allow_redirects=True) as r:
            body = await r.content.read(2 * 1024 * 1024)
            txt = body.decode(r.charset or "utf-8", errors="replace")
            print(f"  [{label}] HTTP {r.status} {len(body)}B")
            if r.status == 200:
                if want_baike:
                    # 页面里有没有真的出现这个词
                    hit = TERM in txt or TERM2 in txt
                    print(f"      命中关键词: {hit}")
                print(f"      {txt[:show].replace(chr(10), ' | ')}")
            return r.status, txt
    except Exception as exc:
        print(f"  [{label}] 异常 {type(exc).__name__}: {str(exc)[:90]}")
        return -1, ""


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 96)
        print("【A】cookie 预热后请求 item")
        print("=" * 96)
        try:
            async with s.get("https://baike.baidu.com/", headers={"User-Agent": UA}) as r:
                print(f"  首页 HTTP {r.status}  Set-Cookie 数: {len(r.headers.getall('Set-Cookie', []))}")
                for c in r.headers.getall("Set-Cookie", [])[:4]:
                    print(f"      {c[:70]}")
        except Exception as exc:
            print(f"  首页异常 {exc}")
        cookies = {c.key: c.value for c in s.cookie_jar}
        print(f"  jar 里: {list(cookies.keys())[:8]}")
        await get(s, f"https://baike.baidu.com/item/{quote(TERM)}", "带cookie", show=250)
        await get(s, f"https://baike.baidu.com/item/{quote(TERM)}", "带cookie+Referer",
                  headers={"Referer": "https://baike.baidu.com/"}, show=250)

        print("\n" + "=" * 96)
        print("【B】百科站内搜索接口")
        print("=" * 96)
        for label, url in [
            ("search/word", f"https://baike.baidu.com/search/word?word={quote(TERM)}"),
            ("search/none", f"https://baike.baidu.com/search/none?word={quote(TERM)}"),
            ("search/word+le", f"https://baike.baidu.com/search/word?word={quote(TERM)}&le=encyclopedia"),
            ("wikiui", f"https://baike.baidu.com/wikiui/api/lemma?title={quote(TERM)}"),
            ("openapi", f"https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020&bk_key={quote(TERM)}&bk_length=600"),
        ]:
            await get(s, url, label, show=320)
            await asyncio.sleep(0.7)

        print("\n" + "=" * 96)
        print("【C】移动独立域名 / 其他百度入口")
        print("=" * 96)
        for label, url, hdr in [
            ("wapbaike", f"https://wapbaike.baidu.com/item/{quote(TERM)}", None),
            ("wapbaike 移动UA", f"https://wapbaike.baidu.com/item/{quote(TERM)}", {"User-Agent": MUA}),
            ("baike.baidu.com/item+aladdin", f"https://baike.baidu.com/item/{quote(TERM)}?fr=aladdin", None),
            ("mbd 百科", f"https://mbd.baidu.com/newspage/data/landingshare?preview=1&pageType=1&nid=news_8834755456208816", None),
        ]:
            await get(s, url, label, headers=hdr, show=250)
            await asyncio.sleep(0.7)

        print("\n" + "=" * 96)
        print("【D】必应 site: 限定复测（关键）")
        print("=" * 96)
        for q in [f"site:baike.baidu.com {TERM}",
                  f"{TERM} 百度百科",
                  f"百度百科 {TERM}",
                  f"{TERM2} 百度百科"]:
            url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count=10"
            async with s.get(url) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
            items = re.findall(r"<item>(.*?)</item>", txt, re.S)
            print(f"\n  查询「{q}」→ {len(items)} 条")
            for it in items[:5]:
                def g(tag):
                    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
                    return (m.group(1).strip() if m else "")
                link = g("link")
                host = link.split("/")[2] if "//" in link else ""
                print(f"      {'★' if 'baike.baidu' in host else ' '} {g('title')[:52]}")
                print(f"        {host:<22} {g('description')[:88]}")
            await asyncio.sleep(0.8)

        print("\n" + "=" * 96)
        print("【E】搜狗百科快照")
        print("=" * 96)
        for label, url in [
            ("搜狗百科", f"https://baike.sogou.com/v{TERM}"),
            ("搜狗百科 item", f"https://baike.sogou.com/m/fullLemma?key={quote(TERM)}"),
        ]:
            await get(s, url, label, show=250, want_baike=True)
            await asyncio.sleep(0.7)


if __name__ == "__main__":
    asyncio.run(main())
