"""百度第二轮：绕风控 + 看清真实 DOM 结构 + 广告位定位。

只读诊断。
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

FULL_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

Q = "鸣潮 珂莱塔 养成攻略"


async def warm(session):
    """先访问首页拿 BAIDUID 等 cookie。"""
    for u in ("https://www.baidu.com/", "https://m.baidu.com/"):
        try:
            async with session.get(u, headers=FULL_HEADERS) as r:
                await r.read()
        except Exception:
            pass
        await asyncio.sleep(0.8)


async def probe(session, name, url, mobile=False, referer=None):
    hdr = dict(FULL_HEADERS)
    if mobile:
        hdr["User-Agent"] = MOB_UA
    if referer:
        hdr["Referer"] = referer
    try:
        async with session.get(url, headers=hdr, allow_redirects=True) as r:
            txt = (await r.content.read(2 * 1024 * 1024)).decode(
                r.charset or "utf-8", errors="replace")
            status, final = r.status, str(r.url)
    except Exception as exc:
        print(f"  {name:<26} ✗ {str(exc)[:60]}")
        return None
    soup = BeautifulSoup(txt, "lxml")
    title = soup.title.get_text(strip=True)[:36] if soup.title else "（无标题）"
    print(f"\n  --- {name} ---")
    print(f"      HTTP {status}  {len(txt):>7}字节  最终 {final[:70]}")
    print(f"      标题: {title}")
    print(f"      a={len(soup.find_all('a'))}  script={len(soup.find_all('script'))}"
          f"  #content_left={len(soup.select('#content_left'))}"
          f"  div.result={len(soup.select('div.result'))}"
          f"  .c-container={len(soup.select('.c-container'))}"
          f"  .c-result={len(soup.select('.c-result'))}")
    if "安全验证" in title or len(txt) < 5000:
        print("      ⚠ 安全验证页 / 内容过短")
        return None
    # class 分布
    cand = {}
    for t in soup.find_all(True):
        for cl in (t.get("class") or []):
            cand[cl] = cand.get(cl, 0) + 1
    top = sorted(cand.items(), key=lambda kv: -kv[1])[:14]
    print("      高频 class:", ", ".join(f"{k}×{v}" for k, v in top))
    # 广告痕迹
    marks = [m for m in ("ec-tuiguang", "tuiguang", "ec-icon-pure-ad",
                          "data-placeid", "广告", "推广", "营销", "commercial")
             if m in txt]
    print("      广告痕迹:", ", ".join(marks) if marks else "（无）")
    return soup, txt


def show(soup, label):
    print(f"      [{label}]")
    picked = 0
    for div in soup.select("div.result, div.c-container, div.c-result"):
        a = div.select_one("h3 a") or div.select_one("a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        ad = bool(div.select_one(".ec-tuiguang, .tuiguang, [data-placeid]"))
        snip = ""
        for sel in (".c-abstract", "[class*='c-abstract']", ".content-right_8Zs40",
                    "[class*='content-right']", ".c-span-last"):
            n = div.select_one(sel)
            if n:
                snip = n.get_text(" ", strip=True)
                break
        print(f"        {'[广告]' if ad else '[自然]'} {title[:50]}")
        if snip:
            print(f"                {snip[:88]}")
        picked += 1
        if picked >= 5:
            break
    if not picked:
        print("        （没抓到结果条）")


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        print("=" * 78)
        print("【A】预热拿 cookie 后的各端点")
        await warm(s)
        names = sorted({m.key for m in s.cookie_jar})
        print(f"  cookie: {names[:12]}")

        got = {}
        got["PC tn=baidurt"] = await probe(
            s, "PC tn=baidurt", f"https://www.baidu.com/s?wd={quote(Q)}&tn=baidurt",
            referer="https://www.baidu.com/")
        await asyncio.sleep(2.0)
        got["PC 普通(带cookie)"] = await probe(
            s, "PC 普通(带cookie)", f"https://www.baidu.com/s?wd={quote(Q)}&rn=10",
            referer="https://www.baidu.com/")
        await asyncio.sleep(2.0)
        got["移动 m.baidu.com"] = await probe(
            s, "移动 m.baidu.com", f"https://m.baidu.com/s?word={quote(Q)}",
            mobile=True, referer="https://m.baidu.com/")
        await asyncio.sleep(2.0)

        print("\n" + "=" * 78)
        print("【B】结果条与广告位")
        for name, g in got.items():
            if not g:
                continue
            soup, txt = g
            print(f"\n  === {name} ===")
            show(soup, "结果条")

        print("\n" + "=" * 78)
        print("【C】百度分词质量（用能通的那个端点）")
        for q in ["鸣潮 珂莱塔 养成攻略", "原神 甘雨 圣遗物 搭配",
                  "崩坏星穹铁道 2.0 角色攻略", "南京 天气 明天"]:
            try:
                async with s.get(f"https://www.baidu.com/s?wd={quote(q)}&tn=baidurt",
                                 headers={**FULL_HEADERS, "Referer": "https://www.baidu.com/"}) as r:
                    txt = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
                soup = BeautifulSoup(txt, "lxml")
            except Exception as exc:
                print(f"  {q:<24} 异常 {str(exc)[:46]}")
                await asyncio.sleep(2.0)
                continue
            items = []
            for div in soup.select("div.result, div.c-container, div.c-result"):
                a = div.select_one("h3 a") or div.select_one("a")
                if not a:
                    continue
                t = a.get_text(" ", strip=True)
                if t:
                    ad = bool(div.select_one(".ec-tuiguang, .tuiguang, [data-placeid]"))
                    items.append((t, ad))
            d = sum(1 for t, _ in items if re.search(
                r"汉语字典|组词|部首|笔顺|五笔|读音|新华字典|汉典|国学", t)
                or re.match(r"^[一-龥]（[^）]{0,12}）_百度百科$", t))
            mark = "★坍缩" if items and d >= max(1, len(items) // 2) else (
                "△可疑" if d else "  正常")
            print(f"  {q:<24} {len(items):>2} 条 词典页 {d} {mark}")
            for t, ad in items[:3]:
                print(f"       {'[广告]' if ad else '[自然]'} {t[:54]}")
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    asyncio.run(main())
