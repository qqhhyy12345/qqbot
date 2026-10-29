"""验证组合方案：百度联想词（sugrec）→ Bing RSS 检索。

思路：百度的中文分词/词库远强于 Bing。用 sugrec 拿到「正确分词后的短查询」，
     再拿这些短查询去 Bing RSS 搜——正好落在实测有效的「专名 + 1 修饰词」区间。
只读诊断。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from urllib.parse import quote, quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept-Encoding": "gzip, deflate"}

DICT_PAT = re.compile(
    r"汉语字典|组词|部首|笔顺|五笔|读音|新华字典|汉典|国学|_百度百科")
SINGLE = re.compile(r"^[一-龥]（[^）]{0,12}）_百度百科$")


def is_dict(t):
    return bool(DICT_PAT.search(t) or SINGLE.match(t))


def parse_rss(xml_text, limit=10):
    import html as _html
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        def pick(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
            return _html.unescape(m.group(1)).strip() if m else ""
        link, title = pick("link"), pick("title")
        if not title or not link.startswith("http"):
            continue
        snip = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", pick("description"))).strip()
        out.append((title, link, snip))
        if len(out) >= limit:
            break
    return out


async def sugrec(session, q):
    url = f"https://www.baidu.com/sugrec?prod=pc&wd={quote(q)}"
    async with session.get(url, headers=H) as r:
        if r.status >= 400:
            return []
        try:
            data = json.loads((await r.content.read(200000)).decode("utf-8", "replace"))
        except Exception:
            return []
    return [g.get("q") for g in (data.get("g") or []) if g.get("q")]


async def bing_rss(session, q, count=8):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url, headers=H) as r:
        if r.status >= 400:
            return []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
    return parse_rss(txt, count)


def quality(res):
    if not res:
        return "0 条"
    d = sum(1 for t, _, _ in res if is_dict(t))
    return f"{len(res):>2} 条 词典页 {d} 可用 {len(res) - d}"


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        queries = [
            "鸣潮 珂莱塔 养成攻略",
            "原神 甘雨 圣遗物 搭配",
            "崩坏星穹铁道 2.0 角色攻略",
            "上海 天气 明天",
        ]
        for q in queries:
            print("\n" + "=" * 74)
            print(f"【原查询】{q}")

            # 1) 直接 Bing（现状）
            base = await bing_rss(s, q)
            print(f"  ① 直接 Bing RSS        → {quality(base)}")
            for t, u, _ in base[:3]:
                print(f"       {'!' if is_dict(t) else ' '} {t[:54]}")

            # 2) 百度联想词
            sugs = await sugrec(s, q)
            print(f"  ② 百度 sugrec 联想词   → {len(sugs)} 个")
            for x in sugs[:6]:
                print(f"       · {x}")
            await asyncio.sleep(0.8)

            # 3) 联想词逐个丢进 Bing
            print("  ③ 联想词 → Bing RSS:")
            merged, seen = [], set()
            for x in sugs[:5]:
                r = await bing_rss(s, x)
                d = sum(1 for t, _, _ in r if is_dict(t))
                good = [y for y in r if not is_dict(y[0])]
                print(f"       {x[:30]:<32} → {len(r):>2}条 词典{d} 可用{len(good)}")
                for t, u, sn in good[:2]:
                    print(f"            ✓ {t[:48]}")
                for t, u, sn in good:
                    if u not in seen:
                        seen.add(u)
                        merged.append((t, u, sn))
                await asyncio.sleep(0.8)

            # 4) 合并结果 vs 直接搜
            d0 = sum(1 for t, _, _ in base if is_dict(t))
            print(f"  ④ 对比：直接搜 可用 {len(base) - d0}/{len(base)}"
                  f"   →  合并后可用 {len(merged)}/{len(merged) + d0}")
            print(f"     合并样本: {[t[:22] for t, _, _ in merged[:5]]}")
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
