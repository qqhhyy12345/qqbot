"""第六轮：细化「百科直达」路径。

上一轮发现：
  - biligame 条目页正文是 JS 渲染 → 抽出来只有 34 字（导航残留）
  - api.php?prop=extracts 无 extract 字段
  - baike.baidu.com 403
  - moegirl / fandom 连接失败（-1，不确定是超时还是 DNS）

要搞清楚：
  1. api.php 到底返回什么？换 action=raw / action=parse 行不行？
  2. 百度百科 403 有没有绕法（移动版 UA？加 Referer？）
  3. moegirl / fandom 的真实错误是什么
  4. **关键**：必应返回的百科类结果，摘要本身信息密度够不够？
     如果够，那就不用抓正文 —— 针对性加强应该落在"挑结果"而不是"抓正文"
"""
import asyncio
import json
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MUA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")


async def probe(session, label, url, headers=None, show=400):
    try:
        async with session.get(url, headers=headers or {}, allow_redirects=True) as r:
            body = await r.content.read(2 * 1024 * 1024)
            txt = body.decode(r.charset or "utf-8", errors="replace")
            print(f"\n  [{label}] HTTP {r.status}  {len(body)} bytes  ctype={r.headers.get('Content-Type','')[:30]}")
            print(f"      {txt[:show].replace(chr(10), ' | ')}")
            return r.status, txt
    except Exception as exc:
        print(f"\n  [{label}] 异常: {type(exc).__name__}: {str(exc)[:110]}")
        return -1, ""


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【一】biligame api.php 各种 action 的实际返回")
        print("=" * 100)
        base = "wiki.biligame.com/ys"
        await probe(s, "api.php 原样",
                    f"https://{base}/api.php?action=query&prop=extracts&explaintext=1&format=json&titles=甘雨", show=300)
        await probe(s, "api.php action=parse",
                    f"https://{base}/api.php?action=parse&page=甘雨&prop=wikitext&format=json", show=300)
        await probe(s, "action=raw",
                    f"https://{base}/index.php?title=甘雨&action=raw", show=300)
        await probe(s, "rest.php",
                    f"https://{base}/api/rest_v1/page/summary/甘雨", show=300)

        print("\n" + "=" * 100)
        print("【二】百度百科 403 的绕法")
        print("=" * 100)
        await probe(s, "桌面 UA",
                    "https://baike.baidu.com/item/原神", show=200)
        await probe(s, "移动 UA",
                    "https://baike.baidu.com/item/原神",
                    headers={"User-Agent": MUA}, show=200)
        await probe(s, "移动 UA + Referer",
                    "https://baike.baidu.com/item/原神",
                    headers={"User-Agent": MUA, "Referer": "https://www.baidu.com/"}, show=200)
        await probe(s, "m.baike",
                    "https://baike.baidu.com/item/%E5%8E%9F%E7%A5%9E",
                    headers={"User-Agent": MUA}, show=200)
        await probe(s, "baike /item 旧路径",
                    "https://baike.baidu.com/item/%E5%8E%9F%E7%A5%9E/9030811",
                    headers={"User-Agent": UA}, show=200)

        print("\n" + "=" * 100)
        print("【三】萌娘百科 / Fandom 的真实错误")
        print("=" * 100)
        await probe(s, "moegirl",
                    "https://baike.moegirl.org.cn/甘雨", show=200)
        await probe(s, "moegirl 移动",
                    "https://baike.moegirl.org.cn/甘雨",
                    headers={"User-Agent": MUA}, show=200)
        await probe(s, "fandom",
                    "https://genshin-impact.fandom.com/wiki/Ganyu", show=200)
        await probe(s, "fandom api",
                    "https://genshin-impact.fandom.com/api.php?action=query&prop=extracts&explaintext=1&format=json&titles=Ganyu", show=300)

        print("\n" + "=" * 100)
        print("【四】关键：必应返回的百科类结果，摘要信息密度够不够？")
        print("=" * 100)
        from urllib.parse import quote_plus
        for q in ["原神 甘雨", "崩坏 星穹铁道", "鸣潮 珂莱塔"]:
            url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count=10"
            async with s.get(url) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
            import re
            items = re.findall(r"<item>(.*?)</item>", txt, re.S)
            print(f"\n  查询「{q}」→ {len(items)} 条")
            for it in items[:6]:
                def g(tag):
                    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
                    return (m.group(1).strip() if m else "")
                title, link, desc = g("title"), g("link"), g("description")
                host = link.split("/")[2] if "//" in link else ""
                mark = "★百科" if any(k in host for k in ("baike", "wiki", "fandom", "moegirl", "biligame")) else "  "
                print(f"    {mark} {title[:52]}")
                print(f"         {host:<24} 摘要 {len(desc):>3} 字: {desc[:100]}")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
