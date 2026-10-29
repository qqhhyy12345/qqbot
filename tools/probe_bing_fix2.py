"""第三轮探测：
1) Bing 中文分词的普遍性（是不是所有中文多词查询都中招）
2) Bing HTML 现在的 DOM 结构（回落路径为何 0 条）
3) 子查询拆分策略是否有效
4) 国内源（360/搜狗/必应中国）真实可达性与质量
只读诊断。
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MOB_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def parse_rss(xml_text, limit=10):
    import html as _html
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml_text, re.S):
        def pick(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
            return _html.unescape(m.group(1)).strip() if m else ""
        link, title = pick("link"), pick("title")
        if not title or not link.startswith("http"):
            continue
        snip = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", pick("description"))).strip()
        out.append((title, link, snip))
        if len(out) >= limit:
            break
    return out


def dictish(title):
    if re.search(r"汉语字典|组词|部首|笔顺|五笔|读音|拼音.*意思|新华字典|汉典|国学", title):
        return True
    if re.match(r"^[一-龥]（[^）]*）_百度百科$", title):
        return True
    if re.match(r"^[一-龥] [a-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü ]+ - ", title):
        return True
    return False


async def rss(session, q, count=10):
    url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss&count={count}"
    async with session.get(url) as r:
        txt = (await r.content.read(2 * 1024 * 1024)).decode(
            r.charset or "utf-8", errors="replace")
    return parse_rss(txt, count)


# ===== 1. 中文分词普遍性 =======================================

async def t1_general(session):
    print("=" * 78)
    print("【1】中文多词查询的普遍性 —— 是不是所有中文都中招")
    cases = [
        ("游戏攻略", "鸣潮 珂莱塔 养成攻略"),
        ("游戏攻略2", "原神 甘雨 圣遗物 搭配"),
        ("中文生活", "南京 天气 明天"),
        ("中文长句", "如何提高Python性能 详细教程"),
        ("中文专名", "科大讯飞 股价"),
        ("纯英文", "wuthering waves coletta build"),
        ("中英混合", "python 性能 优化 技巧"),
    ]
    for name, q in cases:
        try:
            res = await rss(session, q)
        except Exception as exc:
            print(f"  {name:<12} 异常 {str(exc)[:50]}")
            continue
        d = sum(1 for t, u, _ in res if dictish(t))
        mark = "★坍缩" if res and d >= max(1, len(res) // 2) else ("△可疑" if d else "  正常")
        print(f"  {name:<12} {len(res):>2} 条 词典页 {d} {mark}   ← {q}")
        for t, u, _ in res[:3]:
            print(f"        {'!' if dictish(t) else ' '} {t[:56]}")
        await asyncio.sleep(1.0)


# ===== 2. Bing HTML DOM 结构 ==================================

async def t2_html(session):
    print("\n" + "=" * 78)
    print("【2】Bing HTML 现在的结构 —— 为什么 li.b_algo 抓不到")
    for tag, ua, base, extra in (
        ("desktop-www", UA, "https://www.bing.com", "&setlang=zh-CN&ensearch=0"),
        ("mobile-m", MOB_UA, "https://m.bing.com", ""),
        ("desktop-cn", UA, "https://cn.bing.com", "&setlang=zh-CN&ensearch=0"),
    ):
        url = f"{base}/search?q={quote_plus('鸣潮 珂莱塔 养成攻略')}&count=10{extra}"
        try:
            async with session.get(url, headers={"User-Agent": ua}) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode(
                    r.charset or "utf-8", errors="replace")
        except Exception as exc:
            print(f"  {tag:<14} 异常 {str(exc)[:50]}")
            continue
        soup = BeautifulSoup(txt, "lxml")
        print(f"\n  --- {tag}  HTTP {r.status}  {len(txt):>7} 字节 ---")
        print(f"      li.b_algo={len(soup.select('li.b_algo'))}"
              f"  div.b_algo={len(soup.select('div.b_algo'))}"
              f"  .b_algo={len(soup.select('.b_algo'))}"
              f"  h2={len(soup.select('h2'))}"
              f"  a#b_results 链接={len(soup.select('#b_results a[href]'))}"
              f"  ol#b_results={len(soup.select('ol#b_results'))}")
        # 找含链接最多的容器 class
        cand = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            for cl in (a.get("class") or []):
                cand[cl] = cand.get(cl, 0) + 1
            p = a.find_parent(class_=True)
            if p:
                for cl in (p.get("class") or []):
                    cand["P:" + cl] = cand.get("P:" + cl, 0) + 1
        top = sorted(cand.items(), key=lambda kv: -kv[1])[:8]
        print("      高频 class:", ", ".join(f"{k}={v}" for k, v in top))
        # 打印前 3 个外链
        ext = []
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("http") and "bing.com" not in a["href"]:
                ext.append((a.get_text(" ", strip=True), a["href"]))
                if len(ext) >= 4:
                    break
        for t, h in ext:
            print(f"        · {t[:52]}  {h[:60]}")


# ===== 3. 子查询拆分策略 ======================================

async def t3_split(session):
    print("\n" + "=" * 78)
    print("【3】子查询拆分策略 —— 拆成多个短查询分别搜，合并结果")
    groups = [
        ("鸣潮 珂莱塔 养成攻略",
         ["鸣潮 珂莱塔", "珂莱塔 养成", "鸣潮 珂莱塔 攻略"]),
        ("原神 甘雨 圣遗物 搭配",
         ["原神 甘雨", "甘雨 圣遗物", "甘雨 培养"]),
        ("崩坏星穹铁道 2.0 角色攻略",
         ["崩坏星穹铁道 角色", "星穹铁道 2.0", "星穹铁道 角色 攻略"]),
    ]
    for orig, subs in groups:
        print(f"\n  原查询：{orig}")
        merged, seen = [], set()
        for q in subs:
            try:
                res = await rss(session, q)
            except Exception as exc:
                print(f"    子查询 {q!r} 异常 {str(exc)[:40]}")
                continue
            d = sum(1 for t, u, _ in res if dictish(t))
            good = [x for x in res if not dictish(x[0])]
            print(f"    子查询 {q!r:<26} {len(res):>2} 条 词典页 {d}  可用 {len(good)}")
            for t, u, s in good[:2]:
                print(f"         ✓ {t[:52]}")
            for t, u, s in good:
                if u not in seen:
                    seen.add(u)
                    merged.append((t, u, s))
            await asyncio.sleep(1.0)
        print(f"    → 合并去重后可用 {len(merged)} 条")


# ===== 4. 国内源真实情况 ======================================

async def t4_cn_sources(session):
    print("\n" + "=" * 78)
    print("【4】国内源真实情况（dump 页面看是选择器问题还是反爬）")
    q = "鸣潮 珂莱塔 养成攻略"
    e = quote_plus(q)
    srcs = [
        ("so.com(360)", f"https://www.so.com/s?q={e}", UA),
        ("sogou", f"https://www.sogou.com/web?query={e}", UA),
        ("yandex", f"https://yandex.com/search/?text={e}", UA),
        ("quark(夸克)", f"https://quark.sm.cn/s?q={e}", MOB_UA),
        ("toutiao(头条)", f"https://so.toutiao.com/search?keyword={e}", UA),
        ("bing-cn-html", f"https://cn.bing.com/search?q={e}&setlang=zh-CN&ensearch=0", UA),
    ]
    for name, url, ua in srcs:
        try:
            async with session.get(url, headers={"User-Agent": ua}) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode(
                    r.charset or "utf-8", errors="replace")
                status = r.status
        except Exception as exc:
            print(f"  {name:<16} ✗ {type(exc).__name__}: {str(exc)[:56]}")
            continue
        soup = BeautifulSoup(txt, "lxml")
        title = soup.title.get_text(strip=True)[:30] if soup.title else "无标题"
        ext = []
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if h.startswith("http") and not any(d in h for d in ("bing.com", "so.com", "sogou.com", "yandex.", "sm.cn", "toutiao.com", "microsoft", "msn.com")):
                txt2 = a.get_text(" ", strip=True)
                if len(txt2) > 4:
                    ext.append((txt2, h))
        print(f"  {name:<16} HTTP {status}  {len(txt):>7}字节  外链 {len(ext):>3}  「{title}」")
        for t, h in ext[:3]:
            print(f"        · {t[:50]}")
        await asyncio.sleep(1.0)


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        await t1_general(s)
        await t2_html(s)
        await t3_split(s)
        await t4_cn_sources(s)


if __name__ == "__main__":
    asyncio.run(main())
