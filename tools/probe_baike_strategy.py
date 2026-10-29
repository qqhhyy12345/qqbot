"""对比三种抓正文策略的成功率和耗时。

A. 串行重试（固定间隔）
B. 串行重试（随机长间隔，避开爬虫判定）
C. 并发多请求（一次 RTT 拿多个样本）

用法：<AstrBot 的 python> tools/probe_baike_strategy.py 珂莱塔
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
BASE = f"https://wapbaike.baidu.com/item/{quote(TITLE)}"


async def fetch(session, url):
    try:
        async with session.get(
            url, headers={"User-Agent": UA_MOBILE},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            html = (await resp.content.read(2 * 1024 * 1024)).decode(
                resp.charset or "utf-8", errors="replace")
        return html
    except Exception:
        return ""


def body_len(html: str) -> int:
    if 'data-tag="paragraph"' not in html:
        return 0
    return len(re.findall(r'data-tag="paragraph"', html))


async def strategy_serial(session, n, gap_fn):
    best = 0
    for i in range(n):
        html = await fetch(session, BASE)
        best = max(best, body_len(html))
        if best:
            break
        if i < n - 1:
            await asyncio.sleep(gap_fn())
    return best


async def strategy_parallel(session, n):
    htmls = await asyncio.gather(*[fetch(session, BASE) for _ in range(n)])
    return max(body_len(h) for h in htmls)


async def main():
    print(f"目标：{TITLE}")
    async with aiohttp.ClientSession() as session:
        # A 串行 + 固定间隔
        t0 = time.time()
        b = await strategy_serial(session, 7, lambda: 0.4)
        print(f"A 串行7次 间隔0.4s   → 正文段落 {b} 个  用时 {time.time()-t0:.1f}s")

        # B 串行 + 随机长间隔
        t0 = time.time()
        b = await strategy_serial(session, 7, lambda: random.uniform(0.8, 1.6))
        print(f"B 串行7次 随机间隔    → 正文段落 {b} 个  用时 {time.time()-t0:.1f}s")

        # C 并发
        for n in (3, 5, 8):
            t0 = time.time()
            b = await strategy_parallel(session, n)
            print(f"C 并发{n}个            → 正文段落 {b} 个  用时 {time.time()-t0:.1f}s")

        # C2 并发 5 个 × 最多 2 轮
        t0 = time.time()
        b = await strategy_parallel(session, 5)
        if not b:
            b = await strategy_parallel(session, 5)
        print(f"C2 并发5个×2轮        → 正文段落 {b} 个  用时 {time.time()-t0:.1f}s")


asyncio.run(main())
