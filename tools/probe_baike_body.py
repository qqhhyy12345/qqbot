"""百度百科正文抓取诊断：逐个 URL 变体实测，看是 403、反爬还是正文提取失败。

用法：<AstrBot 的 python> tools/probe_baike_body.py "珂莱塔"
"""

import asyncio
import sys

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

TITLE = sys.argv[1] if len(sys.argv) > 1 else "珂莱塔"
from urllib.parse import quote  # noqa: E402

Q = quote(TITLE)

VARIANTS = [
    ("桌面版 item（无 Referer）", f"https://baike.baidu.com/item/{Q}", UA_PC, None),
    ("桌面版 item（带百度 Referer）", f"https://baike.baidu.com/item/{Q}",
     UA_PC, "https://www.baidu.com/"),
    ("桌面版 item（手机 UA）", f"https://baike.baidu.com/item/{Q}", UA_MOBILE, None),
    ("wap 版", f"https://wapbaike.baidu.com/item/{Q}", UA_MOBILE, None),
    ("wap 版（PC UA）", f"https://wapbaike.baidu.com/item/{Q}", UA_PC, None),
    ("m 版", f"https://m.baike.baidu.com/item/{Q}", UA_MOBILE, None),
    ("view 老移动版", f"https://baike.baidu.com/view/{Q}.html", UA_MOBILE, None),
    ("桌面版 + fromModule 参数", f"https://baike.baidu.com/item/{Q}?fromModule=lemma_search-box",
     UA_PC, "https://www.baidu.com/"),
    ("桌面版 + ?fr=aladdin", f"https://baike.baidu.com/item/{Q}?fr=aladdin", UA_MOBILE, None),
    ("互动百科 m 旧域", f"https://wapbaike.baidu.com/view/{Q}.html", UA_MOBILE, None),
]


def longest_text_block(html: str, min_len: int = 200) -> str:
    """复制 baike.py 的实现，看它能提取出多少正文。"""
    import re

    best = ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["p", "div", "span", "section", "article"]):
            if tag.find(["div", "p", "ul", "ol", "table"]):
                continue
            t = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
            if len(t) > len(best):
                best = t
    except Exception as exc:  # noqa: BLE001
        print(f"    (bs4 解析失败: {exc})")
    if len(best) < min_len:
        for m in re.finditer(
            r'"(?:desc|content|abstract|summary)"\s*:\s*"([^"]{%d,})"' % min_len, html
        ):
            t = m.group(1)
            if len(t) > len(best):
                best = t
    return best


async def probe(session, name, url, ua, referer):
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    try:
        async with session.get(url, headers=headers, allow_redirects=True,
                               timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.content.read(2 * 1024 * 1024)
            html = body.decode(resp.charset or "utf-8", errors="replace")
            final = str(resp.url)
            text = longest_text_block(html)
            print(f"  {name}")
            print(f"    状态 {resp.status} | 最终 {final[:80]}")
            print(f"    HTML {len(html)} 字节 | 提取正文 {len(text)} 字符")
            if resp.status == 200:
                head = re.sub(r"\s+", " ", html[:300])
                print(f"    开头: {head[:200]}")
                # 找正文关键词
                for kw in ("珂莱塔", "莫塔里", "鸣潮", "莱塔", "角色", "简介"):
                    if kw in html:
                        print(f"    含「{kw}」")
            return resp.status, len(html), len(text)
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}")
        print(f"    异常: {type(exc).__name__}: {exc}")
        return -1, 0, 0


async def main():
    print("=" * 74)
    print(f"百度百科正文抓取诊断 —— 目标词条：{TITLE}")
    print("=" * 74)
    async with aiohttp.ClientSession() as session:
        for name, url, ua, ref in VARIANTS:
            await probe(session, name, url, ua, ref)
            print()
            await asyncio.sleep(0.6)

        # 同一 URL 重复 4 次，看 wap 版稳定性
        print("=" * 74)
        print("wap 版重复 4 次（看稳定性）")
        print("=" * 74)
        url = f"https://wapbaike.baidu.com/item/{Q}"
        for i in range(4):
            st, hl, tl = await probe(session, f"第 {i + 1} 次", url, UA_MOBILE, None)
            print()
            await asyncio.sleep(0.8)


import re  # noqa: E402

asyncio.run(main())
