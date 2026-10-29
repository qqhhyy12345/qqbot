"""正文提取改进原型：加入链接密度惩罚 + 深度偏好，验证能否救回脏页面。

对比三种打分：
  A 现行（只看 p 文字）
  B 加链接密度惩罚
  C 加链接密度惩罚 + 深度偏好 + 取最深高分容器
只读诊断。
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlparse

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept-Encoding": "gzip, deflate"}

URLS = [
    "https://www.tianqi.com/nanjing/today/",
    "https://www.weather.com.cn/weather/101020100.shtml",
    "https://www.tianqi.com/",
    "https://news.qq.com/",
]

BLOCK_TAGS = ("article", "main", "div", "section", "td", "li")


def depth_of(tag) -> int:
    d = 0
    p = tag.parent
    while p is not None and getattr(p, "name", None) != "[document]":
        d += 1
        p = p.parent
    return d


def score_a(c):
    """现行：只看 p 文字总长"""
    return sum(len(p.get_text(strip=True)) for p in c.find_all("p"))


def score_b(c):
    """加链接密度惩罚"""
    tlen = len(c.get_text(" ", strip=True))
    if tlen < 50:
        return -1
    link_len = sum(len(a.get_text(strip=True)) for a in c.find_all("a"))
    ratio = link_len / tlen if tlen else 1.0
    if ratio > 0.55:
        return -1
    p_len = sum(len(p.get_text(strip=True)) for p in c.find_all("p"))
    score = p_len + (tlen - p_len) * 0.3
    return score * (1 - ratio)


def score_c(c):
    """再加深度偏好"""
    s = score_b(c)
    if s <= 0:
        return -1
    return s / (1 + depth_of(c) * 0.12)


def pick(soup, scorer):
    best, best_s = None, 0
    for c in soup.find_all(BLOCK_TAGS):
        if c.name in ("body", "html"):
            continue
        s = scorer(c)
        if s > best_s:
            best, best_s = c, s
    return best


def pick_deep(soup, scorer, rounds=3):
    """在最优容器内部继续找最优子容器（取最深高分）"""
    cur = pick(soup, scorer)
    for _ in range(rounds):
        if cur is None:
            break
        inner = pick(cur, scorer)
        if inner is not None and inner is not cur:
            cur = inner
        else:
            break
    return cur


def report(name, c, soup):
    if c is None:
        print(f"    {name:<10} 未选中")
        return
    txt = c.get_text("\n", strip=True)
    tlen = len(txt)
    link_len = sum(len(a.get_text(strip=True)) for a in c.find_all("a"))
    ratio = link_len / tlen * 100 if tlen else 0
    ident = (c.get("id") or "") + " " + (
        " ".join(c.get("class") or []) if isinstance(c.get("class"), list)
        else (c.get("class") or ""))
    print(f"    {name:<10} {tlen:>6}字 链接占比 {ratio:>3.0f}%  depth={depth_of(c):>2}  "
          f"[{ident.strip()[:36]}]")
    print(f"      首段: {txt[:150].replace(chr(10), ' | ')}")


async def main():
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        for url in URLS:
            try:
                async with s.get(url, headers=H, allow_redirects=True) as r:
                    html = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
            except Exception as exc:
                print(f"\n{url}  异常 {str(exc)[:60]}")
                continue
            soup = BeautifulSoup(html, "lxml")
            print("\n" + "=" * 74)
            print(f"{url}   {len(html)} 字节")
            report("A 现行", pick(soup, score_a), soup)
            report("B +链接惩罚", pick(soup, score_b), soup)
            report("C +深度", pick_deep(soup, score_c), soup)
            await asyncio.sleep(1.2)


if __name__ == "__main__":
    asyncio.run(main())
