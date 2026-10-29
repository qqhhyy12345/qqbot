"""验证：jieba 分词出的名词片段，必应 RSS 认不认。

核心问题：
  Bing 遇到不认识的专名会单字化 → 词典页 → 被过滤掉 → 没结果。
  jieba 靠自己的词典切词，它认识 ≠ Bing 认识。
  所以必须实测：分词出来的片段丢给 Bing，结果是不是还是垃圾。
"""
import asyncio
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
import jieba
import jieba.posseg as pseg

# 关掉 jieba 的日志噪音
jieba.setLogLevel(60)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---- 从插件源码抠词典页识别逻辑，保证口径一致 ----
import ast
from pathlib import Path

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
        "_is_dict_page", "_parse_rss", "_text_of"
    ):
        exec("\n".join(LINES[n.lineno - 1:n.end_lineno]), NS)

is_dict = NS["_is_dict_page"]
parse_rss = NS["_parse_rss"]
DICT_HOSTS = NS["DICT_HOSTS"]
DICT_TITLE_PAT = NS["DICT_TITLE_PAT"]
BAIKE_ITEM_PAT = NS["BAIKE_ITEM_PAT"]


# ---- 分词候选提取 ----
NOUN_TAGS = {"n", "ns", "nr", "nt", "nz", "vn", "an", "eng", "l", "s", "i", "b"}


def seg_all(q: str) -> list[tuple[str, str]]:
    return [(w, f) for w, f in pseg.cut(q)]


def noun_chunks(q: str) -> list[str]:
    """把连续的名词性词合并成短语（jieba 逐词切分会把专名打散）。"""
    out, buf = [], []
    for w, f in pseg.cut(q):
        if f in NOUN_TAGS and len(w.strip()) > 0:
            buf.append(w.strip())
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


async def rss(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url) as resp:
        if resp.status >= 400:
            return []
        raw = await resp.content.read(2 * 1024 * 1024)
        return parse_rss(raw.decode(resp.charset or "utf-8", errors="replace"), count)


async def main():
    queries = [
        "依安嘉措 大二普",
        "崩坏星穹铁道 2.0 角色攻略",
        "鸣潮 珂莱塔 养成攻略",
        "原神 甘雨 圣遗物 搭配",
        "原神里甘雨这个角色到底怎么培养",
        "崩坏星穹铁道2.0版本新角色有哪些",
        "南京 今天 天气",
        "上海 天气 明天",
    ]

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【第一部分】jieba 分词结果（看它切得对不对）")
        print("=" * 100)
        for q in queries:
            words = seg_all(q)
            chunks = noun_chunks(q)
            print(f"\n查询: {q}")
            print(f"  逐词分词: {[(w, f) for w, f in words]}")
            print(f"  名词短语: {chunks}")

        print("\n" + "=" * 100)
        print("【第二部分】原查询 vs 分词后的候选片段 → Bing 实际结果")
        print("=" * 100)

        for q in queries:
            chunks = noun_chunks(q)
            # 候选片段：名词短语 + 原查询里已有的短 token
            cands = []
            for c in chunks:
                if len(c) >= 2 and c not in cands:
                    cands.append(c)
            # 限 3 个候选，控制请求量
            cands = cands[:3]

            print(f"\n{'=' * 96}\n查询: {q}")

            probes = [q] + cands
            for p in probes:
                res = await rss(s, p)
                d = sum(1 for t, u, _ in res if is_dict(t, u))
                ok = len(res) - d
                mark = "原查询" if p == q else "片段"
                print(f"  [{mark}] {p[:34]:<36} 共 {len(res):>2} 条  词典页 {d:>2}  可用 {ok:>2}")
                for t, u, _ in res[:3]:
                    flag = "×" if is_dict(t, u) else "√"
                    print(f"        {flag} {t[:56]}")
                await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
