"""分析 wapbaike 大页面的结构，找出正文到底藏在哪。

用法：<AstrBot 的 python> tools/probe_baike_dom.py "珂莱塔"
"""

import asyncio
import re
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

TITLE = sys.argv[1] if len(sys.argv) > 1 else "珂莱塔"
URL = f"https://wapbaike.baidu.com/item/{quote(TITLE)}"


async def main():
    best = ""
    async with aiohttp.ClientSession() as session:
        for i in range(6):
            async with session.get(
                URL, headers={"User-Agent": UA_MOBILE},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                html = (await resp.content.read(2 * 1024 * 1024)).decode(
                    resp.charset or "utf-8", errors="replace")
            print(f"第 {i + 1} 次: {resp.status} / {len(html)} 字节")
            if len(html) > len(best):
                best = html
            if len(html) > 15000:
                break
            await asyncio.sleep(0.5)

    print(f"\n最大页面 {len(best)} 字节，保存到临时文件分析")
    with open(r"D:\qqbot\tools\_tmp_baike_page.html", "w", encoding="utf-8") as f:
        f.write(best)

    print("\n--- 关键标记 ---")
    for marker in ["__NEXT_DATA__", "lemma-catalog", "lemma-content",
                   "paraTitle", "J-lemmaContent", "description", "abstract",
                   "window.__", "ld+json", "application/ld+json", "summary",
                   "mainContent", "article-content", "content"]:
        print(f"  {marker:24s} 出现 {best.count(marker)} 次")

    print("\n--- 最长的 class 名（含 content/desc/abstract 的）---")
    for m in sorted(set(re.findall(r'class="([^"]{0,80})"', best))):
        if any(k in m.lower() for k in ("content", "desc", "abstract", "para", "lemma", "summary")):
            print(f"  {m}")

    print("\n--- script 标签类型 ---")
    for m in re.findall(r'<script[^>]*type="([^"]+)"', best):
        print(f"  {m}")

    print("\n--- JSON-LD / ld+json 内容 ---")
    for m in re.findall(
        r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', best, re.S
    ):
        print(f"  {m[:600]}")

    print("\n--- meta description ---")
    for m in re.findall(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', best):
        print(f"  {m[:300]}")

    print("\n--- 最长的 JSON 字符串字段（猜正文）---")
    cands = []
    for m in re.finditer(r'"([A-Za-z_]{2,20})"\s*:\s*"((?:[^"\\]|\\.){200,})"', best):
        cands.append((len(m.group(2)), m.group(1), m.group(2)))
    cands.sort(reverse=True)
    for ln, key, val in cands[:8]:
        print(f"  字段 {key} ({ln} 字符): {val[:180]}")

    print("\n--- 最长的连续文本节点（bs4）---")
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(best, "html.parser")
        nodes = []
        for tag in soup.find_all(True):
            if tag.find(True):
                continue
            t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
            if len(t) > 40:
                nodes.append((len(t), tag.name, t))
        nodes.sort(reverse=True)
        for ln, name, t in nodes[:10]:
            print(f"  <{name}> {ln} 字符: {t[:150]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  bs4 失败: {exc}")


asyncio.run(main())
