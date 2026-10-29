"""第八轮：三个突破口的完整验证。

突破口 A：**必应 site: 限定 + 短查询 = 有效**
  `site:baike.baidu.com 珂莱塔` → 8 条，第一条就是「珂莱塔·莫塔里_百度百科」
  这推翻了之前「site 限定无效」的结论 —— 之前测的是**长查询**下的 site，
  这次是**短查询**下的 site，区别巨大。
  原理：site: 本身成了必应能理解的首 token，不再走中文单字化。

突破口 B：**百度百科开放 API 通了**
  `api/openapi/BaikeLemmaCardApi?...&bk_key=珂莱塔` → 200 纯 JSON
  含 title / desc / card 结构化字段

突破口 C：**wapbaike.baidu.com/item/<词> → 200**（29KB，Next.js SSR）

要验证：
  1. site: 限定的通用性 —— 生僻专名、其它百科站、多个查询
  2. site: + 长查询 是不是真的不行（复现旧结论，确认是"短查询"起的作用）
  3. openapi 的完整内容质量
  4. wapbaike 的正文能不能抽出来
"""
import asyncio
import json
import re
import sys
from urllib.parse import quote, quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 复用插件的正文提取
import ast
from pathlib import Path
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


def parse_rss_items(txt):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", txt, re.S):
        def g(tag):
            m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
            return (m.group(1).strip() if m else "")
        out.append((g("title"), g("link"), g("description")))
    return out


async def rss(s, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with s.get(url) as r:
        if r.status >= 400:
            return []
        txt = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
    return parse_rss_items(txt)


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 104)
        print("【一】site: 限定 + 短查询 —— 通用性验证")
        print("=" * 104)
        cases = [
            ("site:baike.baidu.com 珂莱塔", "生僻专名（必应直接搜会崩）"),
            ("site:baike.baidu.com 依安嘉措", "生僻专名2"),
            ("site:baike.baidu.com 原神", "简单词"),
            ("site:baike.baidu.com 崩坏星穹铁道", "长专名"),
            ("site:baike.baidu.com 甘雨", "游戏角色"),
            ("site:wiki.biligame.com 甘雨", "B站WIKI"),
            ("site:mzh.moegirl.org.cn 珂莱塔", "萌娘百科"),
            ("site:wutheringwaves.fandom.com 珂莱塔", "Fandom"),
        ]
        for q, desc in cases:
            items = await rss(s, q)
            print(f"\n  「{q}」  ({desc}) → {len(items)} 条")
            for t, l, d in items[:3]:
                host = l.split("/")[2] if "//" in l else ""
                print(f"      √ {t[:56]}")
                print(f"        {host:<24} 摘要{len(d):>4}字: {d[:92]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 104)
        print("【二】对照：同样 site: 但查询是长句 → 复现旧结论")
        print("=" * 104)
        for q in ["site:baike.baidu.com 崩坏星穹铁道2.0版本新角色有哪些",
                  "site:baike.baidu.com 原神里甘雨这个角色到底怎么培养"]:
            items = await rss(s, q)
            print(f"\n  「{q}」 → {len(items)} 条")
            for t, l, d in items[:3]:
                print(f"      {'×' if '（汉语' in t or '_汉' in t else '√'} {t[:60]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 104)
        print("【三】site: 拆成两步：先查百科，再补修饰词")
        print("=" * 104)
        for q in ["site:baike.baidu.com 甘雨 配队",
                  "site:baike.baidu.com 甘雨 圣遗物"]:
            items = await rss(s, q)
            print(f"\n  「{q}」 → {len(items)} 条")
            for t, l, d in items[:3]:
                print(f"      √ {t[:56]}")
                print(f"        摘要{len(d):>4}字: {d[:92]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 104)
        print("【四】百度百科开放 API 的完整内容")
        print("=" * 104)
        for term in ["珂莱塔", "依安嘉措", "崩坏星穹铁道", "甘雨"]:
            url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
                   f"&format=json&appid=379020&bk_key={quote(term)}&bk_length=600")
            async with s.get(url) as r:
                body = await r.content.read(2 * 1024 * 1024)
            print(f"\n  「{term}」 HTTP {r.status} {len(body)}B")
            try:
                d = json.loads(body.decode("utf-8", errors="replace"))
                print(f"    title: {d.get('title')}")
                print(f"    desc : {str(d.get('desc'))[:110]}")
                card = d.get("card") or []
                print(f"    card 字段数: {len(card)}")
                for c in card[:6]:
                    val = c.get("value")
                    vs = " / ".join(str(v) for v in val)[:80] if isinstance(val, list) else str(val)[:80]
                    print(f"      {c.get('name','')[:22]:<24} {vs}")
            except Exception as exc:
                print(f"    解析失败: {exc}  原文前 200: {body[:200]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 104)
        print("【五】wapbaike 正文抽取")
        print("=" * 104)
        for term in ["珂莱塔", "崩坏星穹铁道"]:
            url = f"https://wapbaike.baidu.com/item/{quote(term)}"
            async with s.get(url) as r:
                html = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
            try:
                t, text, ratio = extract(html)
            except Exception as exc:
                print(f"  「{term}」 extract 异常 {exc}")
                continue
            print(f"\n  「{term}」 {len(text)} 字 链接占比 {ratio:.0%}")
            print(f"    标题: {t[:60]}")
            print(f"    开头: {text[:220].replace(chr(10), ' | ')}")
            await asyncio.sleep(0.9)


if __name__ == "__main__":
    asyncio.run(main())
