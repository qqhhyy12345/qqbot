"""第四轮：修正后的策略 —— 合并专名 + 剥离虚词 + 结果反馈校验。

第三轮的重大发现（决定最终策略）：
  把长句拆成单词后单独搜，很多会严重跑偏：
    「影神图」→ 搜成电影《影》(腾讯视频/爱奇艺/豆瓣)
    「2.0」  → .NET Framework 2.0
    「材料」 → 材料科学
  原因：必应自己也在单字化 —— 片段越短，越容易被它拆成单字。

  但第二轮又证明：「崩坏星穹铁道」作为**完整短语**单独搜，
  拿到了原查询没有的「崩坏：星穹铁道WIKI_哔哩哔哩」（角色图鉴）。

所以正确策略不是「拆成单词搜」，而是：
  ① 用 jieba 搜索引擎模式 + 连续实义词**合并**出完整专名
  ② 剥掉首尾虚词（「原神里」→「原神」）
  ③ 排除通用词（材料/神话/东西…）
  ④ **用搜回来的标题做反馈校验**：候选词没出现在标题里 → 必应没匹配上 → 该候选作废

第④条是关键：它把"跑偏"从「污染结果」降级为「丢弃该候选」。
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

# ---------------- 策略 v4 ----------------
EDGE_JUNK = ("里", "中", "上", "下", "的", "了", "是", "在", "和", "与", "或", "个", "这", "那", "吗", "呢", "啊")
VAGUE = {
    "角色", "版本", "攻略", "养成", "搭配", "推荐", "资料", "信息", "内容", "方法",
    "教程", "指南", "说明", "介绍", "意思", "什么", "哪些", "怎么", "如何", "时候",
    "结果", "问题", "东西", "情况", "时间", "地方", "所有", "材料", "东西", "神话",
    "影神", "故事", "内容", "具体", "详细", "完整", "一起", "自己", "现在",
}
GOOD_TAGS = {"n", "ns", "nr", "nt", "nz", "vn", "l", "eng"}


def strip_edges(w):
    changed = True
    while changed and len(w) > 2:
        changed = False
        if w[0] in EDGE_JUNK:
            w = w[1:]; changed = True
        if w[-1] in EDGE_JUNK:
            w = w[:-1]; changed = True
    return w


def phrases(query):
    """连续实义词合并成短语 —— 这是保住完整专名的关键。"""
    toks = [(w.strip(), f) for w, f in pseg.cut(query) if w.strip()]
    out, buf = [], []
    for w, f in toks:
        if f in GOOD_TAGS and w not in VAGUE:
            buf.append(w)
        else:
            if buf:
                out.append("".join(buf)); buf = []
    if buf:
        out.append("".join(buf))
    res = []
    for p in out:
        p2 = strip_edges(p)
        if 2 <= len(p2) <= 14 and p2 not in VAGUE and p2 not in res:
            res.append(p2)
    return res


def candidates(query, limit=4):
    """候选：完整短语（含搜索引擎模式的长词），长词优先。"""
    cands = []
    # 搜索引擎模式的长词 = jieba 词典里有的完整专名
    for w in jieba.cut_for_search(query):
        w = strip_edges(w.strip())
        if len(w) >= 3 and w not in VAGUE and w not in cands:
            cands.append(w)
    # 合并短语
    for p in phrases(query):
        if p not in cands and p not in query.split():
            cands.append(p)
    # 按长度降序（长=专名=必应更可能认识）
    cands.sort(key=lambda t: -len(t))
    return cands[:limit]


async def rss(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url) as resp:
        if resp.status >= 400:
            return []
        raw = await resp.content.read(2 * 1024 * 1024)
        return parse_rss(raw.decode(resp.charset or "utf-8", errors="replace"), count)


def hits_query(res, term):
    """搜回来的标题里有没有出现这个候选词 —— 判断必应是否真的匹配上了。"""
    for t, u, sn in res:
        if term in t or term in (sn or ""):
            return True
    return False


async def main():
    cases = [
        "崩坏星穹铁道2.0版本新角色有哪些",
        "原神里甘雨这个角色到底怎么培养",
        "鸣潮珂莱塔这个角色怎么抽到需要什么材料",
        "黑神话悟空影神图在哪里收集全收集点",
        "崩坏星穹铁道3.0版本什么时候更新新角色",
        "上海 天气 明天",
        "原神 甘雨 圣遗物 搭配",
    ]

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【一】v4 候选提取")
        print("=" * 100)
        for q in cases:
            print(f"{q}\n   候选: {candidates(q)}\n")

        print("=" * 100)
        print("【二】端到端：原查询 vs v4 分词（含结果反馈校验）")
        print("=" * 100)

        for q in cases:
            cands = candidates(q)
            print(f"\n{'=' * 96}\n查询: {q}\n候选: {cands}")

            base = await rss(s, q)
            b_d = sum(1 for t, u, _ in base if is_dict(t, u))
            print(f"  A 原查询  : {len(base):>2} 条  词典页 {b_d:>2}  可用 {len(base)-b_d:>2}  (1 请求)")
            for t, u, _ in base[:3]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:62]}")

            merged, kept, dropped = [], 0, []
            for c in cands:
                got = await rss(s, c)
                await asyncio.sleep(0.7)
                if got and hits_query(got, c):
                    merged.extend(got)
                    kept += 1
                else:
                    dropped.append(c)
            cl = clean(merged)
            c_d = sum(1 for t, u, _ in cl if is_dict(t, u))
            print(f"  C v4分词  : {len(cl):>2} 条  词典页 {c_d:>2}  可用 {len(cl)-c_d:>2}"
                  f"  ({1+len(cands)} 请求, 命中 {kept} 候选, 丢弃 {dropped})")
            for t, u, _ in cl[:6]:
                print(f"       {'×' if is_dict(t,u) else '√'} {t[:62]}")


if __name__ == "__main__":
    asyncio.run(main())
