"""百度第三轮：测风控更松的端点 + 联想词 API + 各类页面结构。

只读诊断。
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MOB_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
H = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.baidu.com/",
}

Q = "鸣潮 珂莱塔 养成攻略"


async def hit(session, name, url, headers=None, show_links=0):
    try:
        async with session.get(url, headers=headers or H, allow_redirects=True) as r:
            body = await r.content.read(2 * 1024 * 1024)
            ctype = r.headers.get("Content-Type", "")
            txt = body.decode(r.charset or "utf-8", errors="replace")
            status, final = r.status, str(r.url)
    except Exception as exc:
        print(f"  {name:<24} ✗ {type(exc).__name__}: {str(exc)[:56]}")
        return None
    blocked = any(k in final for k in ("wappass", "captcha", "verify")) or "安全验证" in txt[:3000]
    print(f"  {name:<24} HTTP {status} {len(body):>7}B  {ctype[:24]:<24}"
          f" {'⚠验证页' if blocked else ''}")
    if final.rstrip('/') != url.rstrip('/'):
        print(f"       → 跳到 {final[:76]}")
    if show_links and not blocked:
        soup = BeautifulSoup(txt, "lxml")
        for a in soup.find_all("a", href=True)[:show_links]:
            t = a.get_text(" ", strip=True)
            if t:
                print(f"         · {t[:52]}")
    return txt, blocked


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        # 预热
        for u in ("https://www.baidu.com/", "https://m.baidu.com/"):
            try:
                async with s.get(u, headers=H) as r:
                    await r.read()
            except Exception:
                pass
            await asyncio.sleep(0.6)
        print("=" * 78)
        print("【A】风控较松的百度端点")
        w = quote(Q)
        tests = [
            ("百度新闻搜索 ns", f"https://news.baidu.com/ns?word={w}&tn=news&from=news&cl=2&rn=10", H, 4),
            ("百度新闻 RSS", f"https://news.baidu.com/rss?word={w}&tn=news&rn=10", H, 0),
            ("百度百科搜索", f"https://baike.baidu.com/search/word?word={w}", H, 4),
            ("百度知道搜索", f"https://zhidao.baidu.com/search?word={w}", H, 4),
            ("百度联想词 API", f"https://www.baidu.com/sugrec?prod=pc&wd={w}", H, 0),
            ("百度图片 API", f"https://graph.baidu.com/s?query={w}", H, 0),
            ("百度翻译 API", f"https://fanyi.baidu.com/sugrec?prod=pc&wd={w}", H, 0),
        ]
        for name, url, hdr, nl in tests:
            await hit(s, name, url, hdr, nl)
            await asyncio.sleep(1.8)

        print("\n" + "=" * 78)
        print("【B】PC 域名 + 移动 UA / 无 Referer 变体")
        for name, url, hdr in (
            ("PC域 + 移动UA", f"https://www.baidu.com/s?wd={w}",
             {**H, "User-Agent": MOB_UA, "Referer": "https://m.baidu.com/"}),
            ("PC域 无Referer", f"https://www.baidu.com/s?wd={w}",
             {k: v for k, v in H.items() if k != "Referer"}),
            ("PC域 + tn=monline", f"https://www.baidu.com/s?wd={w}&tn=monline_3_dg",
             {**H, "User-Agent": MOB_UA}),
            ("m.baidu 已预热", f"https://m.baidu.com/s?word={w}",
             {**H, "User-Agent": MOB_UA, "Referer": "https://m.baidu.com/"}),
        ):
            await hit(s, name, url, hdr, 3)
            await asyncio.sleep(2.0)

        print("\n" + "=" * 78)
        print("【C】如果百度新闻/百科通了，看分词质量")
        for label, url, sel in (
            ("百度新闻", f"https://news.baidu.com/ns?word={quote('鸣潮 珂莱塔 养成攻略')}&tn=news&rn=10",
             "div.result h3 a"),
            ("百度百科", f"https://baike.baidu.com/search/word?word={quote('鸣潮 珂莱塔 养成攻略')}",
             "div.dict-section a, div.list h3 a, div.content h3 a"),
        ):
            try:
                async with s.get(url, headers=H) as r:
                    txt = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
            except Exception as exc:
                print(f"  {label}: 异常 {str(exc)[:50]}")
                continue
            soup = BeautifulSoup(txt, "lxml")
            items = [a.get_text(" ", strip=True) for a in soup.select(sel)]
            items = [x for x in items if x]
            print(f"  {label}: {len(items)} 条  {items[:5]}")
            await asyncio.sleep(1.8)


if __name__ == "__main__":
    asyncio.run(main())
