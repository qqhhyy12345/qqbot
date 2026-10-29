"""百度搜索可行性探测 + 广告位识别。

只读诊断，不改任何线上文件。
测：可达性 / 反爬 / 中文分词质量 / 广告位结构 / 摘要质量
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote

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

Q = "鸣潮 珂莱塔 养成攻略"

# 百度结果里的词典页特征（用于判定坍缩）
DICT_PAT = re.compile(
    r"汉语字典|组词|部首|笔顺|五笔|读音|新华字典|汉典|国学|_百度百科")
SINGLE_BAIDU = re.compile(r"^[一-龥]（[^）]{0,12}）_百度百科$")


def is_dictish(t: str) -> bool:
    if DICT_PAT.search(t):
        return True
    if SINGLE_BAIDU.match(t):
        return True
    return False


async def fetch(session, url, ua=UA, extra=None):
    hdr = {"User-Agent": ua}
    if extra:
        hdr.update(extra)
    async with session.get(url, headers=hdr, allow_redirects=True) as r:
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
        return r.status, str(r.url), txt


async def probe_variant(session, name, url, ua=UA):
    try:
        status, final, txt = await fetch(session, url, ua)
    except Exception as exc:
        print(f"  {name:<22} ✗ {type(exc).__name__}: {str(exc)[:70]}")
        return None
    soup = BeautifulSoup(txt, "lxml")
    title = soup.title.get_text(strip=True)[:40] if soup.title else "（无标题）"
    # 百度结果容器
    containers = soup.select("div.result, div.c-container, div[class*='result-op']")
    print(f"  {name:<22} HTTP {status}  {len(txt):>7}字节  容器 {len(containers):>3}  「{title}」")
    return soup, txt, status


def parse_baidu_pc(soup, limit=10):
    """PC 端：#content_left > div.result / div.c-container"""
    out = []
    root = soup.select_one("#content_left") or soup
    for div in root.select("div.result, div.c-container"):
        # 跳过广告
        if div.select_one(".ec-tuiguang, .tuiguang, span.ec-icon-pure-common.ec-icon-pure-ad"):
            continue
        h3 = div.select_one("h3 a") or div.select_one("a")
        if not h3:
            continue
        title = h3.get_text(" ", strip=True)
        href = (h3.get("href") or "").strip()
        if not title:
            continue
        snip = ""
        for sel in (".c-abstract", ".content-right_8Zs40", ".c-span-last span",
                    ".c-line-clamp3", ".c-abstract-encontent"):
            n = div.select_one(sel)
            if n:
                snip = n.get_text(" ", strip=True)
                break
        if not snip:
            n = div.select_one(".c-container span[class*='content-right']")
            if n:
                snip = n.get_text(" ", strip=True)
        out.append((title, href, snip, bool(div.select_one(
            ".ec-tuiguang, .tuiguang, [data-placeid]"))))
        if len(out) >= limit:
            break
    return out


def parse_baidu_mob(soup, limit=10):
    """移动端"""
    out = []
    for div in soup.select("div.c-result, div.result, div[class*='c-result']"):
        a = div.select_one("a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        href = (a.get("href") or "").strip()
        snip = ""
        n = div.select_one("[class*='c-span-last'], .c-abstract, .content-right_8Zs40")
        if n:
            snip = n.get_text(" ", strip=True)
        if title:
            out.append((title, href, snip, False))
        if len(out) >= limit:
            break
    return out


def report(name, res):
    if not res:
        print(f"      （解析出 0 条）")
        return
    d = sum(1 for t, *_ in res if is_dictish(t))
    mark = "★坍缩" if d >= max(1, len(res) // 2) else ("△可疑" if d else "  正常")
    print(f"      → {len(res)} 条  词典页 {d}  {mark}")
    for t, u, s, ad in res[:5]:
        print(f"        {'[广告]' if ad else '      '} {t[:52]}")
        if s:
            print(f"                摘要: {s[:76]}")


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    conn = aiohttp.TCPConnector(limit=4)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA},
                                     cookie_jar=aiohttp.CookieJar(unsafe=True),
                                     connector=conn) as s:
        w = quote(Q)

        print("=" * 78)
        print("【1】各端点可达性")
        variants = [
            ("PC www.baidu.com/s", f"https://www.baidu.com/s?wd={w}", UA),
            ("PC +tn=baidurt", f"https://www.baidu.com/s?wd={w}&tn=baidurt", UA),
            ("移动 m.baidu.com", f"https://m.baidu.com/s?word={w}", MOB_UA),
            ("轻量 baidu.com/sf", f"https://www.baidu.com/s?wd={w}&ie=utf-8&tn=baidu", UA),
        ]
        dumps = {}
        for name, url, ua in variants:
            got = await probe_variant(s, name, url, ua)
            if got:
                dumps[name] = got
            await asyncio.sleep(2.0)

        print("\n" + "=" * 78)
        print("【2】分词质量 + 广告位识别")
        for name, (soup, txt, status) in dumps.items():
            print(f"\n  --- {name} ---")
            pc = parse_baidu_pc(soup)
            mob = parse_baidu_mob(soup)
            res = pc or mob
            report(name, res)
            # 广告痕迹统计
            for marker in ("ec-tuiguang", "tuiguang", "ec-icon-pure-ad",
                           "data-placeid", "广告", "百度营销", "推广", "商业推广"):
                n = txt.count(marker)
                if n:
                    print(f"      痕迹 {marker!r} × {n}")
            await asyncio.sleep(1.0)

        print("\n" + "=" * 78)
        print("【3】百度 vs Bing 分词对比（多组查询）")
        cases = [
            "鸣潮 珂莱塔 养成攻略",
            "原神 甘雨 圣遗物 搭配",
            "崩坏星穹铁道 2.0 角色攻略",
            "南京 天气 明天",
        ]
        for q in cases:
            qq = quote(q)
            try:
                status, final, txt = await fetch(
                    s, f"https://www.baidu.com/s?wd={qq}", UA)
                soup = BeautifulSoup(txt, "lxml")
                res = parse_baidu_pc(soup) or parse_baidu_mob(soup)
            except Exception as exc:
                print(f"  {q:<24} 异常 {str(exc)[:50]}")
                await asyncio.sleep(2.0)
                continue
            d = sum(1 for t, *_ in res if is_dictish(t))
            mark = "★坍缩" if res and d >= max(1, len(res) // 2) else (
                "△可疑" if d else "  正常")
            print(f"  {q:<24} {len(res):>2} 条 词典页 {d} {mark}")
            for t, u, sn, ad in res[:3]:
                print(f"       {'[广告]' if ad else '      '} {t[:56]}")
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    asyncio.run(main())
