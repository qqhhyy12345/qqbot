"""重试命中率测试：Bing 中文坍缩是概率性的，多试几次能不能撞到正常那次。

只读诊断。
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
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


async def one(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url, headers=H) as r:
        if r.status >= 400:
            return []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
    return parse_rss(txt, count)


async def main():
    queries = [
        "鸣潮 珂莱塔 养成攻略",
        "原神 甘雨 圣遗物 搭配",
        "崩坏星穹铁道 2.0 角色攻略",
        "鸣潮 1.4 版本 新角色",
        "鸣潮 珂莱塔 怎么抽",
        "原神 甘雨 武器 推荐",
        "黑神话 悟空 影神图 收集",
        "上海 天气 明天",
    ]
    TRIES = 3
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        print(f"{'查询':<26}{'轮次':<6}{'条数':>5}{'词典页':>7}{'可用':>6}  判定")
        print("-" * 78)
        stats = {"first": 0, "best": 0, "total": 0}
        for q in queries:
            for t in range(1, TRIES + 1):
                res = await one(s, q)
                d = sum(1 for x, _, _ in res if is_dict(x))
                good = len(res) - d
                bad = res and d >= max(1, len(res) // 2)
                print(f"{q[:24]:<26}#{t:<5}{len(res):>5}{d:>7}{good:>6}  "
                      f"{'★坍缩' if bad else ('△' if d else '正常')}")
                if t == 1:
                    stats["first"] += len(res) - d
                    stats["total"] += len(res)
                stats["best"] = max(stats["best"], good)
                await asyncio.sleep(1.3)
            print()

        print("=" * 78)
        print(f"首次搜索平均可用：{stats['first'] / len(queries):.1f} 条")
        print(f"3 次重试内最好：{stats['best']} 条")
        print(f"首次平均总条数：{stats['total'] / len(queries):.1f} 条")


if __name__ == "__main__":
    asyncio.run(main())
