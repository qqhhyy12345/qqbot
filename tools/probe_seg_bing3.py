"""第三轮：聚焦「无空格中文长句」这个真实痛点。

前两轮结论：
  ✓ 「崩坏星穹铁道2.0版本新角色有哪些」→ 切成「崩坏星穹铁道」→ 拿到角色图鉴 WIKI（原查询没有）
  ✓ 「原神 甘雨 圣遗物 搭配」→ 词典页 6→0
  ✗ 「原神里甘雨...」→ head 选成「原神里」→ 跑到「老旧小区拆迁」去了，跑偏
  ✗ 生僻专名「依安嘉措」分词救不了（必应自己也不认识）

这轮要解决的：
  1. head 选择修正：从完整专名里选，剥掉首尾虚词
  2. 相关性护栏：候选词必须与原查询有字符交集，否则就是跑偏
  3. 对比三方：原查询 / 现有 _split_queries / 分词方案
  4. 只用「无空格长句」做统计，这才是真实痛点
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
        "_is_dict_page", "_parse_rss", "_text_of", "_clean_results", "_split_queries"
    ):
        exec("\n".join(LINES[n.lineno - 1:n.end_lineno]), NS)

is_dict = NS["_is_dict_page"]
parse_rss = NS["_parse_rss"]
clean = NS["_clean_results"]
split_existing = NS["_split_queries"]

# ---------- 分词策略 v3 ----------
# 首尾虚词/方位词后缀：切出来的专名常带这些
EDGE_JUNK = ("里", "中", "上", "下", "的", "了", "是", "在", "和", "与", "或", "个", "这", "那")
# 太泛的词，单独搜没意义
VAGUE = {
    "角色", "版本", "攻略", "养成", "搭配", "推荐", "资料", "信息", "内容", "方法",
    "教程", "指南", "说明", "介绍", "意思", "什么", "哪些", "怎么", "如何",
    "结果", "问题", "东西", "情况", "时间", "地方", "新", "旧", "所有",
}
# 有实义的词性
GOOD_TAGS = {"n", "ns", "nr", "nt", "nz", "vn", "l", "eng"}


def strip_edges(w: str) -> str:
    changed = True
    while changed and len(w) > 2:
        changed = False
        if w[0] in EDGE_JUNK:
            w = w[1:]
            changed = True
        if w[-1] in EDGE_JUNK:
            w = w[:-1]
            changed = True
    return w


def key_terms(query: str) -> list[str]:
    """从查询里提取「有实义的关键词」，按重要度排序。

    做法：jieba 搜索引擎模式（长词优先），再用词性过滤掉虚词，
    最后按长度降序（长词通常是专名，更可能是必应认识的）。
    """
    terms = []
    for w, f in pseg.cut(query):
        w = w.strip()
        if not w or f not in GOOD_TAGS:
            continue
        if w in VAGUE:
            continue
        w2 = strip_edges(w)
        if len(w2) < 2 or w2 in VAGUE:
            continue
        if w2 not in terms:
            terms.append(w2)

    # 搜索引擎模式的长词：jieba 词典里有的完整专名
    for w in jieba.cut_for_search(query):
        w = w.strip()
        if len(w) < 3 or w in VAGUE:
            continue
        # 长词必须包含某个已有 term 或本身就是完整专名
        w2 = strip_edges(w)
        if len(w2) >= 3 and w2 not in terms:
            terms.append(w2)

    # 相关性护栏：必须与原查询有字符交集（去掉后还是同一个词的一部分）
    qchars = set(query)
    terms = [t for t in terms if set(t) & qchars]
    # 长词优先
    terms.sort(key=lambda t: -len(t))
    return terms


def seg_candidates(query: str, limit=4):
    """分词方案生成的候选子查询。

    只在「无空格的长句」上生效——有空格时现有 _split_queries 更靠谱。
    """
    if len(query) < 8:
        return []
    terms = key_terms(query)
    cands = []
    for t in terms:
        if t != query and t not in cands:
            cands.append(t)
        if len(cands) >= limit:
            break
    return cands


async def rss(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url) as resp:
        if resp.status >= 400:
            return []
        raw = await resp.content.read(2 * 1024 * 1024)
        return parse_rss(raw.decode(resp.charset or "utf-8", errors="replace"), count)


def quality(res):
    """(总条数, 词典页数, 可用条数)"""
    d = sum(1 for t, u, _ in res if is_dict(t, u))
    return len(res), d, len(res) - d


async def main():
    # 重点：无空格中文长句（真实痛点）
    long_sentences = [
        "崩坏星穹铁道2.0版本新角色有哪些",
        "原神里甘雨这个角色到底怎么培养",
        "崩坏星穹铁道3.0版本什么时候更新新角色",
        "鸣潮珂莱塔这个角色怎么抽到需要什么材料",
        "黑神话悟空影神图在哪里收集全收集点",
    ]
    # 对照组：有空格的（现有方案已经能处理）
    spaced = [
        "原神 甘雨 圣遗物 搭配",
        "鸣潮 珂莱塔 养成攻略",
    ]

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 104)
        print("【一】关键词提取（v3 策略）")
        print("=" * 104)
        for q in long_sentences + spaced:
            print(f"\n{q}")
            print(f"  关键词: {key_terms(q)}")
            print(f"  候选  : {seg_candidates(q)}")

        print("\n" + "=" * 104)
        print("【二】三方对比（长句）")
        print("=" * 104)

        for q in long_sentences:
            cands = seg_candidates(q)
            print(f"\n{'=' * 100}\n查询: {q}\n候选: {cands}")

            base = await rss(s, q)
            b_t, b_d, b_ok = quality(base)
            print(f"  A 原查询      : {b_t:>2} 条  词典页 {b_d:>2}  可用 {b_ok:>2}   (1 请求)")
            for t, u, _ in base[:3]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:60]}")

            ex = split_existing(q)
            if ex:
                merged = []
                for c in ex:
                    merged.extend(await rss(s, c))
                    await asyncio.sleep(0.7)
                cl = clean(merged)
                e_t, e_d, e_ok = quality(cl)
                print(f"  B 现有拆分    : {e_t:>2} 条  词典页 {e_d:>2}  可用 {e_ok:>2}   ({1+len(ex)} 请求)  {ex}")
                for t, u, _ in cl[:3]:
                    print(f"       {'×' if is_dict(t,u) else '√'} {t[:60]}")
            else:
                print(f"  B 现有拆分    : 不适用（无空格，现有方案切不动）")

            merged = []
            for c in cands:
                merged.extend(await rss(s, c))
                await asyncio.sleep(0.7)
            cl = clean(merged)
            s_t, s_d, s_ok = quality(cl)
            print(f"  C 分词方案    : {s_t:>2} 条  词典页 {s_d:>2}  可用 {s_ok:>2}   ({1+len(cands)} 请求)")
            for t, u, _ in cl[:5]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
