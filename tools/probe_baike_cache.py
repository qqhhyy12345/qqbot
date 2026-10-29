"""对比：固定 URL vs 加随机参数，正文页命中率差多少。

用法：<AstrBot 的 python> tools/probe_baike_cache.py 珂莱塔
"""

import asyncio
import random
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

TITLE = sys.argv[1] if len(sys.argv) > 1 else "珂莱塔"
TRIES = 10


def has_para(html: str) -> bool:
    return 'data-tag="paragraph"' in html


async def run(session, mode: str, url_base: str) -> tuple[int, list[int], float]:
    hit, sizes = 0, []
    t0 = time.time()
    for _ in range(TRIES):
        url = url_base
        if mode == "random":
            url = f"{url_base}?t={random.randint(100000, 999999)}"
        async with session.get(
            url, headers={"User-Agent": UA_MOBILE},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            html = (await resp.content.read(2 * 1024 * 1024)).decode(
                resp.charset or "utf-8", errors="replace")
        sizes.append(len(html))
        if has_para(html):
            hit += 1
        await asyncio.sleep(0.35)
    return hit, sizes, time.time() - t0


async def main():
    base = f"https://wapbaike.baidu.com/item/{quote(TITLE)}"
    print(f"目标：{TITLE}   每组 {TRIES} 次")
    async with aiohttp.ClientSession() as session:
        for mode in ("fixed", "random", "fixed2", "random2"):
            hit, sizes, dt = await run(session, mode, base)
            label = "固定 URL  " if mode.startswith("fixed") else "随机参数  "
            print(f"\n{label}命中 {hit}/{TRIES}  用时 {dt:.1f}s")
            print(f"  页面大小: {sizes}")
        print("\n（fixed2/random2 是第二轮，用来排除「前一轮把 CDN 缓存打热」的干扰）")


asyncio.run(main())
