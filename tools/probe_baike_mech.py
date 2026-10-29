"""第十轮：解决两个新问题 + 验证核心机制。

新问题 1：wapbaike 正文抽取不稳定
  上一轮「崩坏星穹铁道」拿到 27202 字，这一轮只 33 字。
  → 必须搞清楚是随机（SSR 时序）还是稳定行为。

新问题 2：专名提取是瓶颈
  jieba 也不认识生僻专名（珂莱塔被切成 珂/莱/塔）——和 Bing 同一个毛病。
  分词插件只能提出候选，不能保证候选正确。

核心机制验证（决定方案成立与否）：
  **用模糊查询从百科反查词条列表**
  搜 `site:baike.baidu.com 鸣潮` → 返回的标题里可能直接有「珂莱塔·莫塔里_百度百科」
  → 这样生僻专名不需要分词正确，只需要「模糊词 + 百科限定」能找到词条群，
     再从词条列表里挑出与上下文匹配的条目。

另外：openapi 的 JSON 截断怎么修。
"""
import asyncio
import ast
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

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


async def rss(s, q, count=15):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with s.get(url) as r:
        if r.status >= 400:
            return []
        txt = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
    out = []
    for it in re.findall(r"<item>(.*?)</item>", txt, re.S):
        def g(tag):
            m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", it, re.S)
            return (m.group(1).strip() if m else "")
        out.append((g("title"), g("link"), g("description")))
    return out


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【一】核心机制：模糊词 + 百科限定 → 反查词条列表")
        print("=" * 100)
        probes = [
            ("site:baike.baidu.com 鸣潮", "找鸣潮系列词条"),
            ("site:baike.baidu.com 崩坏星穹铁道 角色", "找角色词条"),
            ("site:baike.baidu.com 甘雨 原神", "甘雨+游戏限定"),
            ("site:baike.baidu.com 影神图 黑神话", "影神图+游戏限定"),
            ("site:baike.baidu.com 依安嘉措 大二普", "生僻+限定"),
        ]
        for q, desc in probes:
            items = await rss(s, q)
            baike = [(t, l, d) for t, l, d in items if "baike.baidu.com" in l]
            print(f"\n  「{q}」 ({desc}) → {len(items)} 条，其中百科 {len(baike)} 条")
            for t, l, d in baike[:5]:
                print(f"      ★ {t[:58]}")
                print(f"         摘要{len(d):>4}字 {d[:84]}")
            if not baike:
                for t, l, d in items[:2]:
                    print(f"        (非百科) {t[:56]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 100)
        print("【二】wapbaike 正文抽取稳定性（同 URL 抓 3 次）")
        print("=" * 100)
        for term in ["崩坏星穹铁道", "珂莱塔", "原神"]:
            print(f"\n  「{term}」")
            for i in range(3):
                url = f"https://wapbaike.baidu.com/item/{quote(term)}"
                async with s.get(url) as r:
                    html = (await r.content.read(3 * 1024 * 1024)).decode("utf-8", errors="replace")
                    final = str(r.url)
                try:
                    t, text, ratio = extract(html)
                except Exception as exc:
                    print(f"      #{i+1} extract 异常 {exc}")
                    continue
                print(f"      #{i+1} HTML {len(html):>7}B → 正文 {len(text):>6} 字  ratio {ratio:.0%}  标题 {t[:40]}")
                await asyncio.sleep(0.6)

        print("\n" + "=" * 100)
        print("【三】wapbaike 里正文在哪个容器（为什么有时抽不出）")
        print("=" * 100)
        url = f"https://wapbaike.baidu.com/item/{quote('崩坏星穹铁道')}"
        async with s.get(url) as r:
            html = (await r.content.read(3 * 1024 * 1024)).decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        print(f"  HTML 总长 {len(html)}")
        print(f"  含 __NEXT_DATA__: {'__NEXT_DATA__' in html}")
        print(f"  含 window.__: {'window.__' in html}")
        # 找长文本块
        cands = []
        for tag in soup.find_all(["div", "article", "section", "p", "span"]):
            txt = tag.get_text(" ", strip=True)
            if len(txt) > 500:
                ident = (tag.get("id") or "") + " " + (
                    " ".join(tag.get("class") or []) if isinstance(tag.get("class"), list)
                    else (tag.get("class") or ""))
                cands.append((len(txt), tag.name, ident.strip()[:40], txt[:90]))
        cands.sort(key=lambda x: -x[0])
        print(f"  长文本块（>500字）: {len(cands)} 个")
        for ln, nm, ident, head in cands[:6]:
            print(f"    {ln:>7}字 <{nm}> [{ident}]")
            print(f"        {head}")

        print("\n" + "=" * 100)
        print("【四】openapi JSON 截断的修复")
        print("=" * 100)
        for term in ["珂莱塔", "崩坏星穹铁道"]:
            url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
                   f"&format=json&appid=379020&bk_key={quote(term)}&bk_length=600")
            async with s.get(url) as r:
                raw = await r.content.read(2 * 1024 * 1024)
            txt = raw.decode("utf-8", errors="replace")
            # 试 raw_decode
            obj, end = None, 0
            try:
                obj, end = json.JSONDecoder().raw_decode(txt)
                print(f"  {term:<12} raw_decode OK  解析到 {end}/{len(txt)} 字节")
            except Exception as exc:
                print(f"  {term:<12} raw_decode 失败: {exc}")
            if obj:
                print(f"    title={obj.get('title')}  desc={str(obj.get('desc'))[:44]}")
                print(f"    card 数={len(obj.get('card') or [])}")
                for c in (obj.get("card") or [])[:5]:
                    v = c.get("value")
                    vs = " / ".join(str(x) for x in v)[:70] if isinstance(v, list) else str(v)[:70]
                    print(f"      {str(c.get('name'))[:20]:<22} {vs}")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
