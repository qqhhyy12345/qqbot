"""必应中文坍缩的成因探测。

只读诊断：对比不同 host / 语言参数 / 备选源，在同一批中文查询上的结果质量。
不修改任何配置，不写线上目录。

用法：
    python tools/probe_bing_mkt.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

QUERIES = [
    "鸣潮 珂莱塔 养成攻略",
    "鸣潮 1.4 版本 新角色",
    "崩坏星穹铁道 2.0 角色攻略",
    "南京航空航天大学",
]

# 词典页/落地页特征：命中即视为「坍缩」
DICT_PAT = re.compile(
    r"汉语|字典|词典|读音|笔顺|笔画|组词|_百度百科|"
    r"^鸣|萌娘|拼音|部首|五笔|释义",
    re.I,
)


def is_dictish(title: str, url: str) -> bool:
    """判断是不是被当成词条查询的字典/百科页。"""
    if DICT_PAT.search(title):
        return True
    # 站点首页且标题没有任何实质信息
    if re.search(r"https?://[^/]+/?$", url) and len(title) < 12:
        return True
    return False


def parse_rss(xml_text: str, limit: int = 10):
    import html as _html

    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):

        def pick(tag: str) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
            return _html.unescape(m.group(1)).strip() if m else ""

        link, title = pick("link"), pick("title")
        if not title or not link.startswith("http"):
            continue
        snip = re.sub(r"<[^>]+>", " ", pick("description"))
        snip = re.sub(r"\s+", " ", snip).strip()
        out.append((title, link, snip))
        if len(out) >= limit:
            break
    return out


def parse_bing_html(html_text: str, limit: int = 10):
    soup = BeautifulSoup(html_text, "lxml")
    out = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a")
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        href = (link.get("href") or "").strip()
        if not title or not href.startswith("http"):
            continue
        snip = ""
        for sel in ("p", ".b_caption p", ".b_snippet"):
            node = block.select_one(sel)
            if node:
                snip = node.get_text(" ", strip=True)
                break
        out.append((title, href, snip))
        if len(out) >= limit:
            break
    return out


def parse_ddg(html_text: str, limit: int = 10):
    soup = BeautifulSoup(html_text, "lxml")
    out = []
    for res in soup.select("div.result"):
        link = res.select_one("a.result__a")
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        href = (link.get("href") or "").strip()
        snip = ""
        node = res.select_one("a.result__snippet")
        if node:
            snip = node.get_text(" ", strip=True)
        if title and href:
            out.append((title, href, snip))
        if len(out) >= limit:
            break
    return out


# ---- 各个变体 -------------------------------------------------

async def v_rss(session, q, base, extra):
    url = f"{base}?q={quote_plus(q)}&format=rss&count=10" + extra
    async with session.get(url) as r:
        if r.status >= 400:
            return url, []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
        return url, parse_rss(txt)


async def v_html(session, q):
    url = f"https://cn.bing.com/search?q={quote_plus(q)}&count=10&setlang=zh-CN&ensearch=0"
    try:
        async with session.get("https://cn.bing.com/") as warm:
            await warm.read()
    except Exception:
        pass
    async with session.get(url) as r:
        if r.status >= 400:
            return url, []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
        return url, parse_bing_html(txt)


async def v_ddg(session, q):
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"
    async with session.get(url) as r:
        if r.status >= 400:
            return url, []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
        return url, parse_ddg(txt)


VARIANTS = [
    ("A www.rss 裸参数（现状）", lambda s, q: v_rss(s, q, "https://www.bing.com/search", "")),
    ("B www.rss +mkt/setlang", lambda s, q: v_rss(s, q, "https://www.bing.com/search",
                                            "&mkt=zh-CN&setlang=zh-hans&ensearch=0")),
    ("C cn.rss 裸参数", lambda s, q: v_rss(s, q, "https://cn.bing.com/search", "")),
    ("D cn.rss +mkt/setlang", lambda s, q: v_rss(s, q, "https://cn.bing.com/search",
                                             "&mkt=zh-CN&setlang=zh-hans&ensearch=0")),
    ("E www.rss +ensearch/qs=n", lambda s, q: v_rss(s, q, "https://www.bing.com/search",
                                               "&ensearch=0&qs=n&form=QBRE")),
    ("F cn.html 现状回落", v_html),
    ("G duckduckgo html", v_ddg),
]


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        for name, fn in VARIANTS:
            print(f"\n{'=' * 78}\n【{name}】")
            for q in QUERIES:
                try:
                    url, res = await fn(s, q)
                except Exception as exc:
                    print(f"  {q[:20]:<22} 异常 {type(exc).__name__}: {str(exc)[:60]}")
                    await asyncio.sleep(1.0)
                    continue
                if not res:
                    print(f"  {q[:20]:<22} 0 条")
                else:
                    bad = sum(1 for t, u, _ in res if is_dictish(t, u))
                    flag = "★坍缩" if bad >= max(1, len(res) // 2) else (
                        "△可疑" if bad else "  正常")
                    print(f"  {q[:20]:<22} {len(res):>2} 条  词典页 {bad}  {flag}")
                    for t, u, _ in res[:4]:
                        mark = "!" if is_dictish(t, u) else " "
                        print(f"      {mark} {t[:58]}")
                await asyncio.sleep(1.2)


if __name__ == "__main__":
    asyncio.run(main())
