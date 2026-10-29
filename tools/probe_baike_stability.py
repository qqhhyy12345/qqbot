"""统计：各词条抓到带正文页面的概率，以及需要重试几次。

用法：<AstrBot 的 python> tools/probe_baike_stability.py 珂莱塔 崩坏星穹铁道 影神图 原神 甘雨
"""

import asyncio
import re
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

TRIES = 8


def extract(html: str) -> tuple[str, str]:
    """返回 (meta description, 正文段落拼接)"""
    soup = BeautifulSoup(html, "html.parser")
    meta = ""
    for m in soup.find_all("meta", attrs={"name": "description"}):
        meta = m.get("content", "")
        break
    paras = []
    for div in soup.find_all(attrs={"data-tag": "paragraph"}):
        t = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
        if t:
            paras.append(t)
    return meta, "".join(paras)


async def probe(session, title: str) -> None:
    url = f"https://wapbaike.baidu.com/item/{quote(title)}"
    sizes, paras, metas, first_ok = [], 0, "", None
    for i in range(TRIES):
        async with session.get(
            url, headers={"User-Agent": UA_MOBILE},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            html = (await resp.content.read(2 * 1024 * 1024)).decode(
                resp.charset or "utf-8", errors="replace")
        meta, body = extract(html)
        sizes.append(len(html))
        if len(body) > 200:
            paras += 1
            if first_ok is None:
                first_ok = i + 1
        if len(meta) > len(metas):
            metas = meta
        await asyncio.sleep(0.4)
    print(f"\n■ {title}")
    print(f"  {TRIES} 次请求的页面大小: {sizes}")
    print(f"  拿到正文的次数: {paras}/{TRIES}"
          + (f"（首次命中在第 {first_ok} 次）" if first_ok else ""))
    print(f"  正文长度: {len(body)} 字符 | meta 简介: {len(metas)} 字符")
    if metas:
        print(f"  简介开头: {metas[:110]}")


async def main():
    titles = sys.argv[1:] or ["珂莱塔", "崩坏星穹铁道", "影神图", "原神", "甘雨"]
    async with aiohttp.ClientSession() as session:
        for t in titles:
            await probe(session, t)


asyncio.run(main())
