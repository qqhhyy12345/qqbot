"""第五轮：绕过必应，直达百科 / WIKI。

核心思路（换赛道）：
  必应搜「崩坏星穹铁道」→ 单字化 → 「崩（汉语文字）_百度百科」
  但 WIKI 站有稳定 URL 规律：
    wiki.biligame.com/ys/甘雨
    wiki.biligame.com/sr/崩坏：星穹铁道
    baike.baidu.com/item/原神
  → 直接构造 URL，完全绕开必应的中文分词

要验证：
  1. MediaWiki 的 action=query&prop=extracts API 能不能直接拿纯文本（比解析 HTML 干净得多）
  2. 哪些游戏 / 站点有可用前缀
  3. 百度百科 item 页、萌娘百科 能不能抓
  4. 用改好的 _extract 能不能抽出干净正文
"""
import asyncio
import ast
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

SRC = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
NS: dict = {"re": re, "BeautifulSoup": BeautifulSoup}
for n in TREE.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                try:
                    e = ast.Expression(n.value)
                    ast.fix_missing_locations(e)
                    NS[t.id] = eval(compile(e, "<c>", "eval"), {"re": re}, {})
                except Exception:
                    pass
LINES = SRC.splitlines()
for n in TREE.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in (
        "_extract", "_block_stats", "_score_block"
    ):
        exec("\n".join(LINES[n.lineno - 1:n.end_lineno]), NS)

extract = NS["_extract"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 候选：站点前缀 → 页面名
TARGETS = [
    # (站点, 路径前缀, 条目名, 说明)
    ("biligame-ys",   "/ys/",           "甘雨",              "原神"),
    ("biligame-sr",   "/sr/",           "崩坏：星穹铁道",    "星穹铁道"),
    ("biligame-wuwa", "/wutheringwaves/", "珂莱塔",          "鸣潮"),
    ("biligame-bmwy", "/blackmyth/",    "影神图",            "黑神话悟空"),
    ("biligame-zk",   "/zillion/",      "铃兰",              "明日方舟"),
    ("biligame-ak",   "/arknights/",    "能天使",            "明日方舟"),
    ("biligame-my",   "/moyou/",        "夜刀神",            "崩坏3"),
    ("biligame-wf",   "/wuthering/",    "深空之眼",          "战双"),
    ("baidu-baike",   "/item/",         "原神",              "百度百科"),
    ("baidu-baike2",  "/item/",         "崩坏：星穹铁道",    "百度百科"),
    ("moegirl",       "/",              "甘雨",              "萌娘百科"),
    ("fandom-genshin", "/wiki/",        "Ganyu",             "Fandom 原神"),
]

MW_API_HOSTS = [
    "wiki.biligame.com/ys",
    "wiki.biligame.com/sr",
    "wiki.biligame.com/wutheringwaves",
    "wiki.biligame.com/blackmyth",
    "wiki.biligame.com/zillion",
    "baike.moegirl.org.cn",
]


def mw_api_url(host, title):
    return (f"https://{host}/api.php?action=query&prop=extracts"
            f"&explaintext=1&exsectionformat=plain&format=json&titles={quote(title)}")


def page_url(site, prefix, title):
    if site.startswith("biligame"):
        return f"https://wiki.biligame.com{prefix}{quote(title, safe=':')}"
    if site.startswith("baidu"):
        return f"https://baike.baidu.com{prefix}{quote(title)}"
    if site == "moegirl":
        return f"https://baike.moegirl.org.cn/{quote(title)}"
    if site.startswith("fandom"):
        return f"https://genshin-impact.fandom.com{prefix}{quote(title)}"
    return ""


async def fetch(session, url, timeout=25):
    try:
        async with session.get(url, headers={"User-Agent": UA}, allow_redirects=True) as r:
            if r.status >= 400:
                return r.status, b""
            return r.status, await r.content.read(2 * 1024 * 1024)
    except Exception as exc:
        return -1, str(exc).encode()


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 104)
        print("【一】MediaWiki API 直取纯文本（最理想的路径）")
        print("=" * 104)
        for host in MW_API_HOSTS:
            title = {"wiki.biligame.com/ys": "甘雨",
                     "wiki.biligame.com/sr": "崩坏：星穹铁道",
                     "wiki.biligame.com/wutheringwaves": "珂莱塔",
                     "wiki.biligame.com/blackmyth": "影神图",
                     "wiki.biligame.com/zillion": "铃兰",
                     "baike.moegirl.org.cn": "甘雨"}[host]
            url = mw_api_url(host, title)
            st, body = await fetch(s, url)
            ok = "?"
            snippet = ""
            if st == 200:
                try:
                    import json
                    d = json.loads(body.decode("utf-8", errors="replace"))
                    pages = d.get("query", {}).get("pages", {})
                    for _, pg in pages.items():
                        ext = pg.get("extract")
                        if ext:
                            ok = f"extract {len(ext)} 字"
                            snippet = ext[:110].replace("\n", " ")
                        else:
                            ok = "无 extract 字段"
                except Exception as exc:
                    ok = f"解析失败 {exc}"
            else:
                ok = f"HTTP {st}"
            print(f"\n  {host:<32} {title:<14} → {ok}")
            if snippet:
                print(f"      {snippet}")
            await asyncio.sleep(0.8)

        print("\n" + "=" * 104)
        print("【二】直接抓条目页 + 用插件的 _extract 抽正文")
        print("=" * 104)
        for site, prefix, title, desc in TARGETS:
            url = page_url(site, prefix, title)
            if not url:
                continue
            st, body = await fetch(s, url)
            if st != 200:
                print(f"\n  {desc:<12} {title:<14} HTTP {st}")
                continue
            html = body.decode("utf-8", errors="replace")
            try:
                t, text, ratio = extract(html)
            except Exception as exc:
                print(f"\n  {desc:<12} {title:<14} extract 异常 {exc}")
                await asyncio.sleep(0.8)
                continue
            status = "有正文" if len(text) > 200 else f"正文太短({len(text)})"
            print(f"\n  {desc:<12} {title:<14} {status}  {len(text)} 字  链接占比 {ratio:.0%}")
            print(f"      URL: {url[:88]}")
            print(f"      标题: {t[:60]}")
            print(f"      开头: {text[:150].replace(chr(10), ' | ')}")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
