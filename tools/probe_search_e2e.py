"""search_web 完整流程端到端验证（不依赖 AstrBot 框架）。

把 main.py 里的纯函数抠出来 + 复刻 search_web 的两阶段流程，
真跑一遍必应 RSS → 百科兜底，看结果到底对不对题。

用法：<AstrBot 的 python> tools/probe_search_e2e.py "查询1" "查询2"
"""

import asyncio
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

PLUGIN = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url")
SRC = (PLUGIN / "main.py").read_text(encoding="utf-8")

sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")
from astrbot_plugin_fetch_url import baike  # noqa: E402
import jieba  # noqa: E402

jieba.setLogLevel(60)


def grab(fn: str) -> str:
    m = re.search(rf"^(async )?def {fn}\(.*?(?=^(async )?def |^class |\Z)", SRC, re.M | re.S)
    if not m:
        raise RuntimeError(f"抠不出 {fn}")
    return m.group(0)


NS = {
    "re": re, "json": json, "aiohttp": aiohttp, "BeautifulSoup": BeautifulSoup,
    "asyncio": asyncio, "jieba": jieba,
    "baike": baike,
    "_JIEBA": None,
    "urlparse": __import__("urllib.parse", fromlist=["urlparse"]).urlparse,
    "unquote": __import__("urllib.parse", fromlist=["unquote"]).unquote,
    "quote": __import__("urllib.parse", fromlist=["quote"]).quote,
    "quote_plus": __import__("urllib.parse", fromlist=["quote_plus"]).quote_plus,
    "logger": type("L", (), {"warning": lambda *a, **k: None,
                             "info": lambda *a, **k: None,
                             "debug": lambda *a, **k: None})(),
}
# 抠常量
for node in ast.parse(SRC).body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                try:
                    e = ast.Expression(node.value)
                    ast.fix_missing_locations(e)
                    NS[t.id] = eval(compile(e, "<c>", "eval"), {"re": re}, {})
                except Exception:
                    pass
for fn in ("_is_dict_page", "_clean_results", "_relevant", "_enough",
           "_query_entities", "_fetch_rss", "_load_jieba",
           "_parse_rss"):
    exec(grab(fn), NS)

_entities = NS["_query_entities"]
_enough = NS["_enough"]
_clean = NS["_clean_results"]
_fetch_rss = NS["_fetch_rss"]
UA = NS.get("UA", "Mozilla/5.0")
TIMEOUT = NS.get("TIMEOUT", 20)
MIN_USABLE = NS.get("MIN_USABLE", 3)
MAX_RETRIES = NS.get("MAX_RETRIES", 2)
RETRY_DELAY = NS.get("RETRY_DELAY", 1.0)


async def run(query: str) -> None:
    print("=" * 74)
    print(f"查询：{query}")
    entities = _entities(query)
    print(f"  提取到的核心实体：{entities}")
    raw_all = []
    tried = []
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT), headers={"User-Agent": UA}
    ) as session:
        # 阶段 1：原查询 + 条件重试
        for attempt in range(MAX_RETRIES + 1):
            got = await _fetch_rss(session, query, 5)
            tried.append(query)
            raw_all.extend(got)
            cleaned = _clean(raw_all)
            if _enough(cleaned, entities):
                break
            if not got:
                break
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        # 阶段 2：百科兜底（不做查询拆分）
        if not _enough(_clean(raw_all), entities):
            got = await baike.baike_search(session, query, jieba_mod=jieba, count=5)
            if got:
                raw_all.extend(got)
                tried.append("百度百科通道")

    results = _clean(raw_all)
    # 命中查询实体的排前面
    results.sort(key=lambda r: 0 if any(e.lower() in r[0].lower() for e in entities) else 1)
    print(f"  试过：{tried}")
    print(f"  结果 {len(results)} 条：")
    for t, u, s in results[:6]:
        hit = "√" if any(e.lower() in t.lower() for e in entities) else " "
        print(f"   {hit} {t[:56]}")
        print(f"      {u}")
        if s:
            print(f"      {s[:80]}")
    if not results:
        print("   （0 条）")


async def main():
    queries = sys.argv[1:] or [
        "南京航空航天大学有哪些王牌专业",
        "依央嘉措是什么地方",
        "南京 今天 天气",
    ]
    for q in queries:
        await run(q)
        await asyncio.sleep(1.0)


asyncio.run(main())
