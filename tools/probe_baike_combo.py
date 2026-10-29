"""第九轮：确认最终方案的三个组成部分。

已验证的关键事实：
  1. `site:baike.baidu.com <专名>` 在必应里**不会单字化** —— 这是 site: 改变了检索路径
  2. `site:baike.baidu.com 崩坏星穹铁道2.0版本新角色有哪些`（长句）→ 仍然崩
     → **site: 只救短查询**
  3. `site:baike.baidu.com 甘雨 配队` → 2 条全对（专名 + 1 修饰词）
  4. wapbaike.baidu.com/item/<词> → 200，正文完整可抽（崩铁 27202 字）
  5. openapi BaikeLemmaCardApi → 纯 JSON，含 title/desc/结构化 card

所以最终方案 = 分词 + site: 前缀 + 百科直达，三层互补。

这轮要确认：
  A. 「分词后加 site: 前缀」的真实效果 —— 是不是真的救活了长句
  B. wapbaike 用错名字时的行为（404 / 空 / 跳转）→ 能不能盲目构造 URL
  C. openapi 的稳定性（珂莱塔那个 JSON 截断）
  D. 三层组合的端到端效果
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
import jieba
import jieba.posseg as pseg
from bs4 import BeautifulSoup

jieba.setLogLevel(60)

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
        "_extract", "_block_stats", "_score_block", "_is_dict_page"
    ):
        exec("\n".join(LINES[n.lineno - 1:n.end_lineno]), NS)
extract = NS["_extract"]
is_dict = NS["_is_dict_page"]

# ---- 分词：提取专名（复用前面验证过的 v4 策略） ----
EDGE_JUNK = ("里", "中", "上", "下", "的", "了", "是", "在", "和", "与", "或", "个", "这", "那", "吗", "呢", "啊")
VAGUE = {
    "角色", "版本", "攻略", "养成", "搭配", "推荐", "资料", "信息", "内容", "方法",
    "教程", "指南", "说明", "介绍", "意思", "什么", "哪些", "怎么", "如何", "时候",
    "结果", "问题", "东西", "情况", "时间", "地方", "所有", "材料", "神话", "影神",
    "故事", "具体", "详细", "完整", "一起", "自己", "现在", "收集", "全收集点",
}
GOOD_TAGS = {"n", "ns", "nr", "nt", "nz", "vn", "l", "eng"}


def strip_edges(w):
    ch = True
    while ch and len(w) > 2:
        ch = False
        if w[0] in EDGE_JUNK:
            w = w[1:]; ch = True
        if w[-1] in EDGE_JUNK:
            w = w[:-1]; ch = True
    return w


def proper_nouns(query, limit=3):
    """提取可能的专名：连续实义词合并 + 搜索引擎模式长词。"""
    out = []
    for w in jieba.cut_for_search(query):
        w = strip_edges(w.strip())
        if len(w) >= 3 and w not in VAGUE and w not in out:
            out.append(w)
    toks = [(w.strip(), f) for w, f in pseg.cut(query) if w.strip()]
    buf = []
    for w, f in toks:
        if f in GOOD_TAGS and w not in VAGUE:
            buf.append(w)
        else:
            if buf:
                p = strip_edges("".join(buf))
                if 2 <= len(p) <= 14 and p not in VAGUE and p not in out:
                    out.append(p)
                buf = []
    if buf:
        p = strip_edges("".join(buf))
        if 2 <= len(p) <= 14 and p not in VAGUE and p not in out:
            out.append(p)
    # 长词优先，且必须与原查询有交集
    out = [t for t in out if set(t) & set(query)]
    out.sort(key=lambda t: -len(t))
    return out[:limit]


BAIKE_SITES = ["baike.baidu.com", "wiki.biligame.com", "mzh.moegirl.org.cn",
               "wutheringwaves.fandom.com", "wiki.biligame.com"]


async def rss(s, q, count=10):
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
        print("【A】「分词 + site: 前缀」能否救活长句  ← 方案核心")
        print("=" * 100)
        long_q = [
            "崩坏星穹铁道2.0版本新角色有哪些",
            "原神里甘雨这个角色到底怎么培养",
            "鸣潮珂莱塔这个角色怎么抽到需要什么材料",
            "黑神话悟空影神图在哪里收集全收集点",
        ]
        for q in long_q:
            nouns = proper_nouns(q)
            print(f"\n  查询: {q}")
            print(f"  专名: {nouns}")

            # 基线：原查询
            base = await rss(s, q)
            b_d = sum(1 for t, u, _ in base if is_dict(t, u))
            print(f"    原查询            → {len(base):>2} 条  词典页 {b_d:>2}  可用 {len(base)-b_d:>2}")
            for t, l, d in base[:2]:
                print(f"        {'×' if is_dict(t,l) else '√'} {t[:56]}")

            # 方案：site: + 专名
            allres, tried = [], []
            for n in nouns[:2]:
                for site in ["baike.baidu.com"]:
                    sq = f"site:{site} {n}"
                    got = await rss(s, sq)
                    tried.append(sq)
                    allres.extend(got)
                    await asyncio.sleep(0.8)
            c_d = sum(1 for t, u, _ in allres if is_dict(t, u))
            print(f"    site:+专名        → {len(allres):>2} 条  词典页 {c_d:>2}  可用 {len(allres)-c_d:>2}   {tried}")
            for t, l, d in allres[:4]:
                print(f"        {'×' if is_dict(t,l) else '√'} {t[:56]}")
                print(f"           摘要{len(d):>4}字 {d[:80]}")

        print("\n" + "=" * 100)
        print("【B】wapbaike 用错名字时的行为")
        print("=" * 100)
        for term in ["珂莱塔", "珂莱塔·莫塔里", "崩坏星穹铁道", "崩坏：星穹铁道",
                     "甘雨", "不存在的词条xyz123"]:
            url = f"https://wapbaike.baidu.com/item/{quote(term)}"
            try:
                async with s.get(url) as r:
                    html = (await r.content.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
                    st = r.status
                    final = str(r.url)
                try:
                    t, text, ratio = extract(html)
                except Exception as exc:
                    text, ratio, t = f"EXC {exc}", 0, ""
                redir = "跳转" if final.rstrip('/').split('/')[-1] != quote(term) else "无跳转"
                print(f"  {term:<16} HTTP {st}  正文 {len(text):>6} 字  链接占比 {ratio:>4.0%}  {redir}")
                print(f"      标题: {t[:56]}")
                if text:
                    print(f"      开头: {text[:110].replace(chr(10), ' | ')}")
            except Exception as exc:
                print(f"  {term:<16} 异常 {type(exc).__name__}: {str(exc)[:60]}")
            await asyncio.sleep(0.9)

        print("\n" + "=" * 100)
        print("【C】openapi 稳定性")
        print("=" * 100)
        for term in ["珂莱塔", "崩坏星穹铁道", "依安嘉措", "原神", "上海"]:
            url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
                   f"&format=json&appid=379020&bk_key={quote(term)}&bk_length=600")
            async with s.get(url) as r:
                raw = await r.content.read(2 * 1024 * 1024)
            txt = raw.decode("utf-8", errors="replace")
            try:
                d = json.loads(txt)
                print(f"  {term:<14} OK  title={d.get('title')}  desc={str(d.get('desc'))[:40]}  card={len(d.get('card') or [])}")
            except Exception as exc:
                print(f"  {term:<14} 解析失败 ({exc})  {len(txt)}B  尾部: {txt[-60:]!r}")
            await asyncio.sleep(0.8)


if __name__ == "__main__":
    asyncio.run(main())
