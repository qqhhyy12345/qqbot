"""验证并发策略：正文总字符数 + 耗时，多词条。

用法：<AstrBot 的 python> tools/probe_baike_parallel.py 珂莱塔 影神图 原神
"""

import asyncio
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def extract(html: str) -> tuple[int, int]:
    """返回 (段落数, 正文总字符数)"""
    if 'data-tag="paragraph"' not in html:
        return 0, 0
    soup = BeautifulSoup(html, "html.parser")
    paras = []
    for div in soup.find_all(attrs={"data-tag": "paragraph"}):
        for junk in div.select(
            "[class*='refSup'], [class*='discussionTag'], i[data-refid]"
        ):
            junk.decompose()
        t = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
        if t:
            paras.append(t)
    return len(paras), len("".join(paras))


async def fetch(session, url):
    try:
        async with session.get(
            url, headers={"User-Agent": UA_MOBILE},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            return (await resp.content.read(2 * 1024 * 1024)).decode(
                resp.charset or "utf-8", errors="replace")
    except Exception:
        return ""


async def main():
    titles = sys.argv[1:] or ["珂莱塔", "影神图", "原神"]
    async with aiohttp.ClientSession() as session:
        for title in titles:
            url = f"https://wapbaike.baidu.com/item/{quote(title)}"
            for wave in range(2):
                t0 = time.time()
                htmls = await asyncio.gather(*[fetch(session, url) for _ in range(5)])
                dt = time.time() - t0
                stats = [extract(h) for h in htmls]
                best = max(stats, key=lambda s: s[1])
                print(f"{title:10s} 第{wave + 1}轮 并发5个 用时{dt:.2f}s "
                      f"段落数={[s[0] for s in stats]} "
                      f"字数={[s[1] for s in stats]} → 最好 {best[1]} 字")
                if best[1] >= 400:
                    break
                await asyncio.sleep(0.6)


asyncio.run(main())
