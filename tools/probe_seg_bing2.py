"""第二轮 v2：候选生成策略验证。

第一轮的关键发现（决定策略方向）：
  「崩坏星穹铁道」被 jieba 切成 崩坏/星穹/铁道 → 搜「崩坏」毫无意义
  → 不能简单把分词结果当独立查询，必须「合并成短语」+「短语配修饰词」

要验证三件事：
  1. 合并后的名词短语质量（会不会出「依安」这种碎片）
  2. 「短语」单独搜 vs 「短语+修饰词」组合搜，哪个更有效
  3. 端到端收益 & 请求成本
"""
import asyncio
import ast
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
import jieba
import jieba.posseg as pseg

jieba.setLogLevel(60)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SRC = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)
NS: dict = {"re": re}
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
        "_is_dict_page", "_parse_rss", "_text_of", "_clean_results"
    ):
        exec("\n".join(LINES[n.lineno - 1:n.end_lineno]), NS)

is_dict = NS["_is_dict_page"]
parse_rss = NS["_parse_rss"]
clean = NS["_clean_results"]

# 可以当「修饰词」的部分（要能独立成查询才有意义）
MODIFIER_TAGS = {"n", "ns", "nt", "nz", "vn", "v", "a", "l"}
# 这些词本身太泛，单独搜没价值
VAGUE = {
    "角色", "版本", "攻略", "养成", "搭配", "推荐", "资料", "信息", "内容", "方法",
    "教程", "指南", "说明", "介绍", "意思", "意思", "什么", "哪些", "怎么", "如何",
    "结果", "问题", "东西", "情况", "时间", "地方",
}


def segments_of(q):
    """先按空格切段（模型常带空格），再对每段分词"""
    return [s for s in q.split() if s.strip()]


def phrases_of(seg):
    """把一段文本切成「有实义的短语」。

    关键：不切碎——jieba 逐词切会把「崩坏星穹铁道」拆成 崩坏/星穹/铁道，
    但这些词在必应上单独搜意义不大。要合并成短语。
    """
    toks = [(w.strip(), f) for w, f in pseg.cut(seg) if w.strip()]
    out, buf = [], []
    for w, f in toks:
        if f in MODIFIER_TAGS and w not in VAGUE:
            buf.append(w)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    # 去掉太长的（多半是把整句连起来了）
    return [p for p in out if 2 <= len(p) <= 12]


def build_candidates(q, limit=4):
    """候选子查询：短语 + 各修饰词的组合。"""
    cands = []

    def add(s):
        s = s.strip()
        if s and s != q and s not in cands and len(cands) < limit:
            cands.append(s)

    for seg in segments_of(q):
        # 搜索引擎模式的长词（能保住完整专名就优先用）
        search_words = [w.strip() for w in jieba.cut_for_search(seg) if w.strip()]
        longs = [w for w in search_words if len(w) >= 3 and not w.isdigit()]
        phrases = phrases_of(seg)

        # 主词 = 最长的那个（通常是专名）
        head = None
        if longs:
            # 挑最长的、且是名词性的
            for w in sorted(longs, key=len, reverse=True):
                if any(w in p or p in w for p in phrases):
                    head = w
                    break
        if head is None and phrases:
            head = phrases[0]
        if head is None:
            head = seg

        add(head)  # 主词单独搜
        # 主词 + 其他修饰词
        for p in phrases:
            if p == head or p in head:
                continue
            if len(p) >= 2:
                add(f"{head} {p}")
    return cands


async def rss(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url) as resp:
        if resp.status >= 400:
            return []
        raw = await resp.content.read(2 * 1024 * 1024)
        return parse_rss(raw.decode(resp.charset or "utf-8", errors="replace"), count)


async def main():
    queries = [
        "崩坏星穹铁道2.0版本新角色有哪些",
        "原神里甘雨这个角色到底怎么培养",
        "崩坏星穹铁道 2.0 角色攻略",
        "鸣潮 珂莱塔 养成攻略",
        "原神 甘雨 圣遗物 搭配",
        "依安嘉措 大二普",
    ]

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【一】短语提取 & 候选生成")
        print("=" * 100)
        for q in queries:
            print(f"\n查询: {q}")
            for seg in segments_of(q):
                print(f"  段「{seg}」→ 短语 {phrases_of(seg)}")
            print(f"  → 候选: {build_candidates(q)}")

        print("\n" + "=" * 100)
        print("【二】端到端对比")
        print("=" * 100)

        for q in queries:
            cands = build_candidates(q)
            print(f"\n{'=' * 96}\n查询: {q}\n候选: {cands}")

            base = await rss(s, q)
            b_d = sum(1 for t, u, _ in base if is_dict(t, u))
            b_ok = len(base) - b_d
            print(f"  原查询    : {len(base):>2} 条  词典页 {b_d:>2}  可用 {b_ok:>2}")
            for t, u, _ in base[:2]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:62]}")

            merged, tried = [], 1
            for c in cands:
                got = await rss(s, c)
                tried += 1
                merged.extend(got)
                await asyncio.sleep(0.7)
            cl = clean(merged)
            c_d = sum(1 for t, u, _ in cl if is_dict(t, u))
            c_ok = len(cl) - c_d
            print(f"  分词方案  : {len(cl):>2} 条  词典页 {c_d:>2}  可用 {c_ok:>2}  (请求 {tried})")
            for t, u, _ in cl[:5]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:62]}")


if __name__ == "__main__":
    asyncio.run(main())
