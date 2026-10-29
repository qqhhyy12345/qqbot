"""第二轮探测：查 Bing 坍缩的内部成因 + 外部替代源可达性。

只读诊断，不改任何线上文件。
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

MOB_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


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


# ===== 批次 1：Bing 内部 =========================================

async def b1_dump_rss(session, q="鸣潮 珂莱塔 养成攻略"):
    """把 RSS 原文 dump 出来，看 Bing 到底返回了什么条目。"""
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count=10"
    async with session.get(url) as r:
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
    print(f"--- RSS 原文（{len(txt)} 字节）---")
    for i, (t, l, s) in enumerate(parse_rss(txt, 12), 1):
        print(f"{i:>2}. {t[:62]}")
        print(f"    {l[:88]}")
        print(f"    {s[:88]}")
    # 看 <channel> 里有没有「相关搜索」类的字段
    for tag in ("search:SearchTerms", "SearchTerms", "totalItems", "Items"):
        for m in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", txt, re.S)[:3]:
            print(f"  [{tag}] {m[:120]}")


async def b1_variants(session):
    """同一意图的不同写法，看哪种能绕过拆词。"""
    variants = [
        ("原样 空格分隔", "鸣潮 珂莱塔 养成攻略"),
        ("引号包专名", '"鸣潮" "珂莱塔" 养成攻略'),
        ("全部引号", '"鸣潮 珂莱塔 养成攻略"'),
        ("连写无空格", "鸣潮珂莱塔养成攻略"),
        ("只留专名", "鸣潮 珂莱塔"),
        ("英文+中文混合", "鸣潮 珂莱塔 养成攻略 guide"),
        ("加 site 限定", "鸣潮 珂莱塔 养成攻略 site:bilibili.com"),
        ("换同义表述", "鸣潮 珂莱塔 培养指南"),
    ]
    print("\n--- 查询改写变体测试 ---")
    for name, q in variants:
        url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count=10"
        try:
            async with session.get(url) as r:
                if r.status >= 400:
                    print(f"  {name:<16} HTTP {r.status}")
                    continue
                txt = (await r.content.read(2 * 1024 * 1024)).decode(
                    r.charset or "utf-8", errors="replace")
        except Exception as exc:
            print(f"  {name:<16} 异常 {str(exc)[:50]}")
            continue
        res = parse_rss(txt, 10)
        dictish = sum(1 for t, u, _ in res if _dict(t))
        mark = "★坍缩" if dictish >= max(1, len(res) // 2) else ("△可疑" if dictish else "  正常")
        print(f"  {name:<16} {len(res):>2} 条 词典页 {dictish} {mark}")
        for t, u, _ in res[:3]:
            print(f"       {'!' if _dict(t) else ' '} {t[:58]}")
        await asyncio.sleep(1.0)


def _dict(title: str) -> bool:
    """严格的坍缩特征：单字词条 / 字典条目说明。"""
    if re.search(r"汉语字典|组词|部首|笔顺|五笔|读音|拼音.*意思", title):
        return True
    m = re.match(r"^([一-龥])（[^）]*）_百度百科$", title)
    if m:
        return True
    return False


async def b1_html_dump(session):
    """HTML 回落为什么 0 条？dump 页面结构。"""
    for tag, ua, base in (("desktop-www", UA, "https://www.bing.com"),
                           ("desktop-cn", UA, "https://cn.bing.com"),
                           ("mobile-m", MOB_UA, "https://m.bing.com")):
        url = f"{base}/search?q={quote_plus('鸣潮 珂莱塔 养成攻略')}&count=10&setlang=zh-CN&ensearch=0"
        try:
            async with session.get(url, headers={"User-Agent": ua}) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode(
                    r.charset or "utf-8", errors="replace")
        except Exception as exc:
            print(f"  {tag:<14} 异常 {str(exc)[:60]}")
            continue
        soup = BeautifulSoup(txt, "lxml")
        algo = soup.select("li.b_algo")
        hrefs = soup.select("h2 a")
        print(f"  {tag:<14} HTTP {r.status}  {len(txt):>7} 字节  li.b_algo={len(algo)}  h2 a={len(hrefs)}"
              f"  title={soup.title.get_text(strip=True)[:34] if soup.title else '无'}")
        for a in hrefs[:3]:
            print(f"       · {a.get_text(' ', strip=True)[:56]}")


# ===== 批次 2：外部源 =========================================

def parse_generic(soup, sel_title, sel_snip, base=""):
    out = []
    for link in soup.select(sel_title):
        href = (link.get("href") or "").strip()
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        snip = ""
        node = link.find_next(class_=sel_snip) if sel_snip else None
        if node:
            snip = node.get_text(" ", strip=True)
        out.append((title, href, snip))
    return out


async def probe_source(session, name, url, parser, mobile=False):
    try:
        hdr = {"User-Agent": MOB_UA} if mobile else {"User-Agent": UA}
        async with session.get(url, headers=hdr) as r:
            if r.status >= 400:
                print(f"  {name:<22} HTTP {r.status}")
                return
            txt = (await r.content.read(2 * 1024 * 1024)).decode(
                r.charset or "utf-8", errors="replace")
    except Exception as exc:
        msg = str(exc)[:70].replace("\n", " ")
        print(f"  {name:<22} ✗ {type(exc).__name__}: {msg}")
        return
    try:
        res = parser(txt)
    except Exception as exc:
        print(f"  {name:<22} 解析失败 {str(exc)[:50]}")
        return
    dictish = sum(1 for t, u, _ in res if _dict(t))
    mark = "★坍缩" if res and dictish >= max(1, len(res) // 2) else "  可用"
    print(f"  {name:<22} {len(res):>2} 条 词典页 {dictish} {mark}")
    for t, u, _ in res[:3]:
        print(f"       {'!' if _dict(t) else ' '} {t[:56]}")


async def b2_sources(session, q):
    print(f"\n--- 外部源可达性（查询：{q}）---")

    def ddg_lite(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for a in soup.select("a.result-link"):
            out.append((a.get_text(" ", strip=True), (a.get("href") or "").strip(), ""))
        return out

    def mojeek(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for a in soup.select("ul.results-standard li a.title"):
            out.append((a.get_text(" ", strip=True), (a.get("href") or "").strip(), ""))
        return out

    def yandex(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for a in soup.select("a.OrganicTitle-Link"):
            out.append((a.get_text(" ", strip=True), (a.get("href") or "").strip(), ""))
        return out

    def so_com(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for h3 in soup.select("h3.res-title a"):
            out.append((h3.get_text(" ", strip=True), (h3.get("href") or "").strip(), ""))
        return out

    def sogou(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for h3 in soup.select("h3.vr-title a"):
            out.append((h3.get_text(" ", strip=True), (h3.get("href") or "").strip(), ""))
        return out

    def searx(txt):
        soup = BeautifulSoup(txt, "lxml")
        out = []
        for h3 in soup.select("article h3 a"):
            out.append((h3.get_text(" ", strip=True), (h3.get("href") or "").strip(), ""))
        return out

    e = quote_plus(q)
    await probe_source(session, "lite.duckduckgo.com", f"https://lite.duckduckgo.com/lite/?q={e}", ddg_lite)
    await probe_source(session, "mojeek.com", f"https://www.mojeek.com/search?q={e}", mojeek)
    await probe_source(session, "yandex.com", f"https://yandex.com/search/?text={e}", yandex)
    await probe_source(session, "so.com(360)", f"https://www.so.com/s?q={e}", so_com)
    await probe_source(session, "sogou", f"https://www.sogou.com/web?query={e}", sogou)
    for inst in ("https://searx.be", "https://search.bus-hit.me", "https://priv.au"):
        try:
            await probe_source(session, f"searx:{inst.split('//')[1][:12]}",
                              f"{inst}/search?q={e}&format=json", searx)
        except Exception as exc:
            print(f"  searx {inst}: {str(exc)[:40]}")


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 78)
        print("批次 1：Bing 内部")
        await b1_dump_rss(s)
        await b1_variants(s)
        print("\n--- HTML 回落 0 条之谜 ---")
        await b1_html_dump(s)

        print("\n" + "=" * 78)
        await b2_sources(s, "鸣潮 珂莱塔 养成攻略")


if __name__ == "__main__":
    asyncio.run(main())
