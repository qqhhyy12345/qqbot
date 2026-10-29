"""第十二轮：百度百科站内搜索能力（方案的最后一块拼图）。

已确认：
  - `site:baike.baidu.com <专名>` 有效，因为它占掉了必应的首 token 位置，
    让查询词的第一个内容 token 正常检索
  - 但 `site:` + 生僻词仍会崩（影神图 → 影）
  - openapi 能给出正式词条名（珂莱塔 → 珂莱塔·莫塔里）

缺的一块：**百度百科自己的搜索**。
如果百科站内搜索能用，那就能绕开必应分词，直接拿到词条候选列表。

要测的入口：
  1. /search/none?word=  （之前 200 但看着像首页，需细看）
  2. /search/word?word=  （403，看看有没有绕法）
  3. 搜索建议接口
  4. wikiui 搜索接口
  5. 各种 openapi 变体
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

WORD = "珂莱塔"


async def probe(s, label, url, headers=None, show=420, kw=WORD):
    try:
        async with s.get(url, headers=headers or {}, allow_redirects=True) as r:
            body = await r.content.read(3 * 1024 * 1024)
            txt = body.decode(r.charset or "utf-8", errors="replace")
            final = str(r.url)
            hit = kw in txt
            print(f"  [{label}] HTTP {r.status} {len(body):>7}B  关键词命中={hit}")
            if r.status == 200:
                print(f"      {txt[:show].replace(chr(10), ' | ')}")
                # 提取可能的词条链接
                links = re.findall(r'href="(/item/[^"]+)"[^>]*>([^<]{1,40})', txt)
                if links:
                    print(f"      → 词条链接 {len(links)} 个: {[l[1] for l in links[:6]]}")
            return r.status, txt
    except Exception as exc:
        print(f"  [{label}] 异常 {type(exc).__name__}: {str(exc)[:80]}")
        return -1, ""


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【一】百科站内搜索各种入口")
        print("=" * 100)
        await probe(s, "search/none",
                    f"https://baike.baidu.com/search/none?word={quote(WORD)}")
        await probe(s, "search/word 移动UA",
                    f"https://baike.baidu.com/search/word?word={quote(WORD)}",
                    headers={"User-Agent": MUA})
        await probe(s, "search/none 移动UA",
                    f"https://baike.baidu.com/search/none?word={quote(WORD)}",
                    headers={"User-Agent": MUA})

        print("\n" + "=" * 100)
        print("【二】百科搜索建议 / wikiui 搜索接口")
        print("=" * 100)
        await probe(s, "api/searchui",
                    f"https://baike.baidu.com/api/searchui?word={quote(WORD)}")
        await probe(s, "wikiui/searchresult",
                    f"https://baike.baidu.com/wikiui/api/searchresult?word={quote(WORD)}")
        await probe(s, "wikiui/searchresult+page",
                    f"https://baike.baidu.com/wikiui/api/searchresult?page=1&word={quote(WORD)}")
        await probe(s, "suggestion",
                    f"https://baike.baidu.com/api/suggestion?word={quote(WORD)}")
        await probe(s, "searchapi",
                    f"https://baike.baidu.com/api/searchapi?word={quote(WORD)}")

        print("\n" + "=" * 100)
        print("【三】wapbaike 站内搜索 + openapi 其它端点")
        print("=" * 100)
        await probe(s, "wapbaike search",
                    f"https://wapbaike.baidu.com/search?word={quote(WORD)}")
        await probe(s, "wapbaike search/none",
                    f"https://wapbaike.baidu.com/search/none?word={quote(WORD)}")
        await probe(s, "openapi LemmaCardApi(不加bk_key)",
                    "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020")
        await probe(s, "openapi BaikeLemmaCardApi 变体",
                    f"https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020&bk_key={quote(WORD)}&bk_length=1000")

        print("\n" + "=" * 100)
        print("【四】反向验证：把百科词条名拼回必应，site: 是否稳定")
        print("=" * 100)
        for q in [f"site:baike.baidu.com {WORD}",
                  f"site:baike.baidu.com {WORD} 鸣潮",
                  "site:baike.baidu.com 影神图 黑神话悟空",
                  "site:baike.baidu.com 影神图"]:
            url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count=10"
            async with s.get(url) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
            items = re.findall(r"<item>(.*?)</item>", txt, re.S)
            print(f"\n  「{q}」 → {len(items)} 条")
            for it in items[:4]:
                def g(tag):
                    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
                    return (m.group(1).strip() if m else "")
                link = g("link")
                host = link.split("/")[2] if "//" in link else ""
                print(f"      {'★百科' if 'baike.baidu' in host else '      '} {g('title')[:56]}")
            await asyncio.sleep(0.9)


if __name__ == "__main__":
    asyncio.run(main())
