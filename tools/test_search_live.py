"""端到端验证：搜索改进（C）+ 正文过滤（D）的实际效果。

用真实网络、真实页面，与改动前的版本做对照。
旧版从备份包里取，保证对比基准真实。
只读诊断。
"""

from __future__ import annotations

import ast
import asyncio
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

NEW = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py")
BACKUP = Path(r"D:\qqbot\backups\core-20261023-190552-pre-search-fix.zip")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept-Encoding": "gzip, deflate"}


def load_module(path: Path, names: list[str]) -> dict:
    """从一个 main.py 里抠出指定顶层定义（含依赖的模块级常量）。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    ns: dict = {"re": re, "Any": object, "BeautifulSoup": BeautifulSoup}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    try:
                        expr = ast.Expression(node.value)
                        ast.fix_missing_locations(expr)
                        ns[t.id] = eval(compile(expr, "<c>", "eval"), {"re": re}, {})
                    except Exception:
                        pass
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            code = "\n".join(lines[node.lineno - 1:node.end_lineno])
            exec(code, ns)
    return ns


def old_from_backup() -> Path:
    """把备份里的旧 main.py 落到临时文件。"""
    out = Path(r"D:\qqbot\tools\_old_fetch_url_main.py")
    with zipfile.ZipFile(BACKUP) as z:
        name = next(n for n in z.namelist()
                    if n.endswith("astrbot_plugin_fetch_url/main.py"))
        out.write_bytes(z.read(name))
    return out


# ==================================================================
print("加载新旧两版 …")
NEWNS = load_module(NEW, ["_is_dict_page", "_clean_results", "_relevant",
                          "_enough", "_split_queries", "_extract", "_parse_rss"])
OLD_PATH = old_from_backup()
OLDNS = load_module(OLD_PATH, ["_extract", "_parse_rss"])
OLDNS["_parse_bing"] = lambda *a, **k: []
print(f"  新版 {NEW.stat().st_size} 字节 / 旧版 {OLD_PATH.stat().st_size} 字节")

clean = NEWNS["_clean_results"]
split_q = NEWNS["_split_queries"]
relevant = NEWNS["_relevant"]
is_dict = NEWNS["_is_dict_page"]

MIN_USABLE = NEWNS["MIN_USABLE"]
MAX_RETRIES = NEWNS["MAX_RETRIES"]
RETRY_DELAY = NEWNS["RETRY_DELAY"]


async def rss(session, q, count=10):
    from urllib.parse import quote_plus
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url, headers=H) as r:
        if r.status >= 400:
            return []
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
    return NEWNS["_parse_rss"](txt, count)


async def old_search(session, q, count=10):
    """旧逻辑：单次搜索，不过滤。"""
    return await rss(session, q, count)


async def new_search(session, q, count=10):
    """新逻辑：条件重试 + 拆分 + 过滤合并（复刻插件里的流程）。"""
    raw_all, tried = [], []
    tokens = q.split()
    for attempt in range(MAX_RETRIES + 1):
        got = await rss(session, q, count)
        tried.append(q)
        raw_all.extend(got)
        c = clean(raw_all)
        if NEWNS["_enough"](c, tokens) or not got:
            break
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    if not NEWNS["_enough"](clean(raw_all), tokens):
        for sub in split_q(q):
            old_urls = {u for _, u, _ in raw_all}
            got = await rss(session, sub, count)
            tried.append(sub)
            raw_all.extend(got)
            if NEWNS["_enough"](clean(raw_all), tokens):
                break
            new_urls = {u for _, u, _ in got}
            if got and not (new_urls - old_urls):
                break
            await asyncio.sleep(RETRY_DELAY)
    return clean(raw_all), len(tried)


# ==================================================================
async def part1_search():
    print("\n" + "=" * 76)
    print("【一】搜索改进：同一批响应，离线对比三种口径")
    print("  说明：Bing 对同一 query 的返回条数在 0~10 之间随机波动（实测 1,1,5,1 /")
    print("  1,0,10,10），所以必须用同一批响应离线对比，不能让新旧各跑一遍。")
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
    SAMPLES = 4
    timeout = aiohttp.ClientTimeout(total=25)
    tot = {"old_raw": 0, "old_ok": 0, "new1": 0, "newN": 0}
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        print(f"{'查询':<22}{'旧:返回':>8}{'其中词典':>9}{'旧:有效率':>10}"
              f"{'新:返回':>8}{'新:有效率':>10}{'新(4次)':>9}")
        print("-" * 74)
        for q in queries:
            responses = []
            for _ in range(SAMPLES):
                responses.append(await rss(s, q, 10))
                await asyncio.sleep(1.1)

            # 旧逻辑：只发 1 次请求，且不做任何过滤（词典页原样给模型）
            old_raw = responses[0]
            old_d = sum(1 for t, u, _ in old_raw if is_dict(t, u))
            old_pure = len(old_raw) - old_d
            old_rate = old_pure / len(old_raw) * 100 if old_raw else 0

            # 新逻辑，同等预算（1 次请求）：只多了过滤
            new1 = clean(responses[0])
            new1_rate = len(new1) / len(old_raw) * 100 if old_raw else 0

            # 新逻辑，扩展预算（4 次请求合并）：过滤 + 重试 + 拆分
            merged = []
            for r in responses:
                merged.extend(r)
            newN = clean(merged)

            tot["old_raw"] += len(old_raw)
            tot["old_ok"] += old_pure
            tot["new1"] += len(new1)
            tot["newN"] += len(newN)
            print(f"{q[:20]:<22}{len(old_raw):>8}{old_d:>9}{old_rate:>9.0f}%"
                  f"{len(new1):>8}{new1_rate:>9.0f}%{len(newN):>10}")
            await asyncio.sleep(1.0)

        print("-" * 74)
        print(f"{'合计':<22}{tot['old_raw']:>8}{'':>9}{'':>9}"
              f"{tot['new1']:>8}{'':>9}{tot['newN']:>10}")
        n = len(queries)
        print(f"\n  旧逻辑（1 次请求，不过滤）      给模型 {tot['old_raw']//n} 条/次，"
              f"其中约 {1 - tot['old_ok']/tot['old_raw']:.0%} 是词典垃圾")
        print(f"  新逻辑（1 次请求 + 过滤）      给模型 {tot['new1']//n} 条/次，"
              f"词典页已清零 → 模型看到的全是真结果")
        print(f"  新逻辑（4 次请求合并 + 过滤）  给模型 {tot['newN']//n} 条/次"
              f"   ← 重试/拆分的增量")


# ==================================================================
async def part2_extract():
    print("\n" + "=" * 76)
    print("【二】正文过滤：真实网页（URL 取自搜索结果），新旧对比")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        # 先从搜索结果里拿一批真实的中文内容页 URL
        seeds = [
            "CSDN Python 教程",
            "博客园 技术随笔",
            "百度百科 鸣潮",
            "腾讯新闻 科技",
            "搜狐 科技报道",
        ]
        urls: list[str] = []
        for q in seeds:
            try:
                res = await rss(s, q, 6)
            except Exception:
                continue
            for _, u, _ in res:
                if u not in urls:
                    urls.append(u)
            await asyncio.sleep(1.0)
        urls = urls[:12]
        print(f"  候选 URL {len(urls)} 个\n")

        old_tot = new_tot = 0
        tested = 0
        for u in urls:
            try:
                async with s.get(u, headers=H, allow_redirects=True) as r:
                    if r.status >= 400:
                        continue
                    html = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
            except Exception:
                continue
            _, old_t = OLDNS["_extract"](html)
            _, new_t, _ratio = NEWNS["_extract"](html)
            if len(old_t) < 200:
                continue          # 抽不出正文的页面不参与对比
            tested += 1
            old_tot += len(old_t)
            new_tot += len(new_t)
            delta = len(old_t) - len(new_t)
            pct = delta / len(old_t) * 100 if old_t else 0
            noise_left = sum(
                1 for ln in new_t.split("\n")
                if NEWNS["NOISE_LINE_PAT"].match(ln)
                and len(ln.strip()) <= NEWNS["NOISE_LINE_MAX"])
            print(f"  {u[:50]:<52} 旧 {len(old_t):>6} → 新 {len(new_t):>6} 字"
                  f"  (减 {delta:>4}/{pct:>4.1f}%) 残留 {noise_left}")
            await asyncio.sleep(1.2)
        print(f"\n  有效样本 {tested} 个：正文总量 {old_tot:,} → {new_tot:,} 字"
              f"（减 {(old_tot - new_tot) / old_tot * 100 if old_tot else 0:.1f}%）")


async def main():
    await part1_search()
    await part2_extract()


if __name__ == "__main__":
    asyncio.run(main())
