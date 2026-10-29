"""百度第四轮：确认 PC域+移动UA 页面里有没有真结果 + 联想词 API 内容。"""

from __future__ import annotations

import asyncio
import json
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
BASE = {
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(
            timeout=timeout, cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        # 预热拿 cookie
        for u in ("https://www.baidu.com/", "https://m.baidu.com/"):
            try:
                async with s.get(u, headers={**BASE, "User-Agent": UA}) as r:
                    await r.read()
            except Exception:
                pass
            await asyncio.sleep(0.6)

        print("=" * 76)
        print("【A】PC域 + 移动UA 的页面到底有什么")
        w = quote("鸣潮 珂莱塔 养成攻略")
        for label, url, hdr in (
            ("PC域+移动UA", f"https://www.baidu.com/s?wd={w}",
             {**BASE, "User-Agent": MOB_UA, "Referer": "https://m.baidu.com/"}),
            ("PC域+移动UA+tn=monline", f"https://www.baidu.com/s?wd={w}&tn=monline_3_dg",
             {**BASE, "User-Agent": MOB_UA, "Referer": "https://www.baidu.com/"}),
        ):
            try:
                async with s.get(url, headers=hdr) as r:
                    txt = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
            except Exception as exc:
                print(f"  {label}: 异常 {str(exc)[:50]}")
                continue
            soup = BeautifulSoup(txt, "lxml")
            title = soup.title.get_text(strip=True)[:36] if soup.title else "无标题"
            print(f"\n  --- {label}  {len(txt)}字节  「{title}」 ---")
            cand = {}
            for t in soup.find_all(True):
                for cl in (t.get("class") or []):
                    cand[cl] = cand.get(cl, 0) + 1
            print("      高频 class:", ", ".join(
                f"{k}×{v}" for k, v in sorted(cand.items(), key=lambda kv: -kv[1])[:12]))
            print(f"      a={len(soup.find_all('a'))}  h3={len(soup.select('h3'))}"
                  f"  #content_left={len(soup.select('#content_left'))}")
            for sel in ("h3 a", "#content_left a", "div.result h3 a"):
                got = [a.get_text(" ", strip=True) for a in soup.select(sel)]
                got = [g for g in got if g]
                if got:
                    print(f"      [{sel}] {len(got)} 条: {got[:4]}")
            if not soup.find_all("a"):
                print("      可见文本:", re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:200])
            await asyncio.sleep(2.0)

        print("\n" + "=" * 76)
        print("【B】百度联想词 API 返回内容（能否用来做查询扩展）")
        for q in ["鸣潮 珂莱塔 养成攻略", "珂莱塔", "鸣潮"]:
            try:
                async with s.get(
                        f"https://www.baidu.com/sugrec?prod=pc&wd={quote(q)}",
                        headers={**BASE, "User-Agent": UA}) as r:
                    txt = (await r.content.read(200000)).decode("utf-8", errors="replace")
            except Exception as exc:
                print(f"  {q}: 异常 {str(exc)[:50]}")
                await asyncio.sleep(1.5)
                continue
            print(f"\n  查询 {q!r} → {len(txt)} 字节")
            print("   ", txt[:400].replace("\n", " "))
            await asyncio.sleep(1.5)


if __name__ == "__main__":
    asyncio.run(main())
