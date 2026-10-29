"""验证：用 data-tag="paragraph" 属性定位正文段落，能否稳定拿到正文。

用法：<AstrBot 的 python> tools/probe_baike_para.py "珂莱塔"
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

TITLE = sys.argv[1] if len(sys.argv) > 1 else "珂莱塔"
URL = f"https://wapbaike.baidu.com/item/{quote(TITLE)}"


def extract_by_attr(html: str) -> list[str]:
    """按 data-tag="paragraph" 抽正文段落。"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for div in soup.find_all(attrs={"data-tag": "paragraph"}):
        t = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
        if t:
            out.append(t)
    return out


def extract_old(html: str) -> str:
    """旧逻辑：叶子容器取最长文本块（复刻 baike._longest_text_block）。"""
    best = ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["p", "div", "span", "section", "article"]):
        if tag.find(["div", "p", "ul", "ol", "table"]):
            continue
        t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if len(t) > len(best):
            best = t
    return best


async def main():
    print(f"目标：{TITLE}")
    async with aiohttp.ClientSession() as session:
        for i in range(6):
            async with session.get(
                URL, headers={"User-Agent": UA_MOBILE},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                html = (await resp.content.read(2 * 1024 * 1024)).decode(
                    resp.charset or "utf-8", errors="replace")

            paras = extract_by_attr(html)
            old = extract_old(html)
            joined = "".join(paras)
            print(f"第 {i + 1} 次: {resp.status} / {len(html)} 字节")
            print(f"    data-tag=paragraph 段落数: {len(paras)}  合计 {len(joined)} 字符")
            print(f"    旧逻辑(叶子容器最长块): {len(old)} 字符")
            if paras:
                print(f"    首段: {paras[0][:160]}")
            if len(joined) > 200:
                print("\n=== 判定：正文拿到了 ===")
                print(f"全文（{len(joined)} 字符）:\n{joined[:900]}")
                return
            print()
            await asyncio.sleep(0.5)
    print("\n六次都没拿到正文段落")


asyncio.run(main())
