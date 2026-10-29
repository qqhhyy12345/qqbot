"""验证：Bing 对同一 query 的重复请求，返回条数会不会递减？

这直接决定 search_web 的「同查询重试」策略该保留还是该删掉。
只读诊断。
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept-Encoding": "gzip, deflate"}


def parse_count(xml_text) -> int:
    import html as _html
    n = 0
    for block in __import__("re").findall(r"<item>(.*?)</item>", xml_text, __import__("re").S):
        m = __import__("re").search(r"<link>(.*?)</link>", block, __import__("re").S)
        t = __import__("re").search(r"<title>(.*?)</title>", block, __import__("re").S)
        if m and t and _html.unescape(m.group(1)).strip().startswith("http"):
            n += 1
    return n


async def main():
    queries = [
        "鸣潮 珂莱塔 养成攻略",
        "崩坏星穹铁道 2.0 角色攻略",
        "上海 天气 明天",
        "原神 甘雨 圣遗物 搭配",
    ]
    GAP = 1.2
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        print(f"{'查询':<24}{'#1':>5}{'#2':>5}{'#3':>5}{'#4':>5}   趋势")
        print("-" * 60)
        for q in queries:
            counts = []
            for i in range(4):
                url = (f"https://www.bing.com/search?q={quote_plus(q)}"
                       f"&format=rss&count=10")
                async with s.get(url, headers=H) as r:
                    txt = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
                counts.append(parse_count(txt))
                await asyncio.sleep(GAP)
            trend = "递减" if counts[0] > counts[-1] else (
                "持平" if counts[0] == counts[-1] else "波动")
            print(f"{q[:22]:<24}" + "".join(f"{c:>5}" for c in counts) + f"   {trend}")
            await asyncio.sleep(GAP)


if __name__ == "__main__":
    asyncio.run(main())
