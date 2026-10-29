"""第十四轮：闭环验证 —— 正式词条名回搜必应。

方案雏形（三层）：
  L1 词条名校正器：分词出候选专名 → 问 openapi → 拿**正式词条名** + desc + 结构化 card
     关键价值：openapi 能给分词给不出的名字
       「珂莱塔」→ 正式名「珂莱塔·莫塔里」，desc 里还带「鸣潮」上下文
  L2 必应百科通道：用**正式词条名**去搜 `site:baike.baidu.com <正式名>`
     关键价值：正式名是百科自己的规范名，必应能匹配；site: 占掉首 token 绕开单字化
  L3 wapbaike 正文：用正式名拼 URL 抓正文（不稳定，要重试）

这轮验证 L1→L2 的闭环是否真的比原查询好，以及几个实现细节：
  a) jieba cut_for_search 能不能保住「珂莱塔」（cut 切碎了）
  b) 用正式词条名搜必应 vs 用原查询搜必应
  c) wapbaike 用正式名抓正文的稳定性
"""
import asyncio
import json
import re
import sys
from urllib.parse import quote, quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
import jieba
import jieba.posseg as pseg

jieba.setLogLevel(60)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def openapi(s, term):
    url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
           f"&format=json&appid=379020&bk_key={quote(term)}&bk_length=600")
    async with s.get(url) as r:
        raw = await r.content.read(3 * 1024 * 1024)
    txt = raw.decode("utf-8", errors="replace")
    try:
        obj, _ = json.JSONDecoder().raw_decode(txt)
        return obj
    except Exception:
        return None


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


DICT_MARK = re.compile(r"汉语字典|汉语词典|汉典|汉语国学|组词|部首|笔顺|_百度百科$")


def is_dict(t, l):
    if "baike.baidu.com" in l and re.search(r"[（(](?:汉语|HS|\d)", t):
        return True
    return bool(DICT_MARK.search(t)) and "汉语" in t


def candidates(query, limit=4):
    """分词出候选专名（合并连续实义词 + 搜索引擎模式长词）"""
    out = []
    for w in jieba.cut_for_search(query):
        w = w.strip()
        if len(w) >= 2 and w not in out:
            out.append(w)
    toks = [(w.strip(), f) for w, f in pseg.cut(query) if w.strip()]
    buf = []
    for w, f in toks:
        if f in {"n", "ns", "nr", "nt", "nz", "vn", "l"}:
            buf.append(w)
        else:
            if buf:
                p = "".join(buf)
                if 2 <= len(p) <= 14 and p not in out:
                    out.append(p)
                buf = []
    if buf:
        p = "".join(buf)
        if 2 <= len(p) <= 14 and p not in out:
            out.append(p)
    out = [t for t in out if set(t) & set(query)]
    out.sort(key=lambda t: -len(t))
    return out[:limit]


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 104)
        print("【a】jieba 能不能保住「珂莱塔」")
        print("=" * 104)
        for t in ["鸣潮珂莱塔这个角色怎么抽到需要什么材料", "崩坏星穹铁道2.0版本新角色有哪些"]:
            print(f"\n  {t}")
            print(f"    cut        : {[w for w in jieba.cut(t) if w.strip()]}")
            print(f"    cut_for_search: {[w for w in jieba.cut_for_search(t) if w.strip()][:12]}")
            print(f"    候选       : {candidates(t)}")

        print("\n" + "=" * 104)
        print("【b】闭环：候选 → openapi 正式名 → 必应 site: 搜")
        print("=" * 104)
        queries = [
            "鸣潮珂莱塔这个角色怎么抽到需要什么材料",
            "崩坏星穹铁道2.0版本新角色有哪些",
            "黑神话悟空影神图在哪里收集全收集点",
            "原神里甘雨这个角色到底怎么培养",
        ]
        for q in queries:
            print(f"\n{'=' * 100}\n  原查询: {q}")
            cands = candidates(q)
            print(f"  候选: {cands}")

            # 基线
            base = await rss(s, q)
            b_d = sum(1 for t, l, _ in base if is_dict(t, l))
            print(f"\n  ── 基线（必应原查询）: {len(base)} 条, 词典页 {b_d}, 可用 {len(base)-b_d}")
            for t, l, d in base[:3]:
                print(f"      {'×' if is_dict(t,l) else '√'} {t[:58]}")

            # L1: openapi 校正
            canonical = []
            for c in cands:
                obj = await openapi(s, c)
                await asyncio.sleep(0.5)
                if obj and obj.get("title"):
                    title = obj["title"]
                    desc = str(obj.get("desc") or "")
                    tag = "★" if title != c else " "
                    print(f"      L1 {tag} {c:<16} → 正式名「{title}」 desc={desc[:34]}")
                    if title not in canonical:
                        canonical.append(title)
                else:
                    print(f"      L1   {c:<16} → 未命中")

            # L2: 用正式名回搜必应
            merged = []
            for cn in canonical[:2]:
                for q2 in (f"site:baike.baidu.com {cn}", cn):
                    got = await rss(s, q2)
                    await asyncio.sleep(0.7)
                    merged.extend(got)
            # 去重
            seen, uniq = set(), []
            for t, l, d in merged:
                if l in seen:
                    continue
                seen.add(l)
                uniq.append((t, l, d))
            m_d = sum(1 for t, l, _ in uniq if is_dict(t, l))
            print(f"\n  ── 百科通道: {len(uniq)} 条, 词典页 {m_d}, 可用 {len(uniq)-m_d}")
            for t, l, d in uniq[:5]:
                print(f"      {'×' if is_dict(t,l) else '√'} {t[:58]}")
                print(f"         摘要{len(d):>4}字 {d[:78]}")

        print("\n" + "=" * 104)
        print("【c】wapbaike 用正式名抓正文的稳定性")
        print("=" * 104)
        for term in ["崩坏：星穹铁道", "珂莱塔·莫塔里", "影神图"]:
            best = 0
            for i in range(4):
                url = f"https://wapbaike.baidu.com/item/{quote(term)}"
                async with s.get(url) as r:
                    html = (await r.content.read(3 * 1024 * 1024)).decode("utf-8", errors="replace")
                txts = re.findall(r">([^<>]{300,})<", html)
                ln = max((len(t) for t in txts), default=0)
                best = max(best, ln)
                await asyncio.sleep(0.5)
            print(f"  {term:<16} 4 次里最长文本块 {best}")


if __name__ == "__main__":
    asyncio.run(main())
