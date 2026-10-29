"""第十一轮：实现细节定案。

已确认的机制（写进插件设计）：
  ① `site:` 前缀的真正作用 = 把查询首 token 换成英文 → **绕开中文单字化**
     但 site: 的目标站限定必应并不严格执行，作用是「加权」不是「过滤」
  ② 必应返回的**百科摘要信息密度很高**（甘雨 89~120 字、崩铁 113 字、影 125 字）
  ③ 开放 API 能给出**百度百科的正式词条名**：
       bk_key=珂莱塔 → title=珂莱塔·莫塔里（分词给不出的，它能给）
  ④ wapbaike 正文**不稳定**（同 URL 3 次：24800 / 0 / 28 字）→ 必须重试

要定的三个实现细节：
  A. openapi 的 JSON 截断怎么修（bk_length 参数的作用）
  B. wapbaike 重试几次能稳定拿到正文
  C. openapi 对不存在的词返回什么（区分「百科没这个词条」vs「网络失败」）
"""
import asyncio
import json
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def openapi(s, term, extra=""):
    url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
           f"&format=json&appid=379020&bk_key={quote(term)}&bk_length=600{extra}")
    async with s.get(url) as r:
        raw = await r.content.read(2 * 1024 * 1024)
    return r.status, raw.decode("utf-8", errors="replace")


def try_parse(txt):
    """先 raw_decode，失败再正则抠。返回 (ok, title, desc, ncard)"""
    try:
        obj, _ = json.JSONDecoder().raw_decode(txt)
        return True, obj.get("title"), obj.get("desc"), len(obj.get("card") or [])
    except Exception:
        pass
    m = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
    d = re.search(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
    n = txt.count('"name"')
    return False, (m.group(1) if m else None), (d.group(1) if d else None), n


import re


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 100)
        print("【A】openapi 的 bk_length 参数与截断")
        print("=" * 100)
        for term in ["崩坏星穹铁道", "珂莱塔", "原神"]:
            print(f"\n  ── {term}")
            for extra in ["", "&bk_length=600", "&bk_length=1200", "&bk_length=2000",
                          "&bk_length=100&bk_length=600"]:
                st, txt = await openapi(s, term, extra)
                ok, title, desc, n = try_parse(txt)
                print(f"     extra={extra or '(无)':<28} HTTP {st} {len(txt):>6}B  "
                      f"{'完整' if ok else '截断'}  title={str(title)[:26]}  card字段~{n}")
                await asyncio.sleep(0.5)

        print("\n" + "=" * 100)
        print("【B】wapbaike 重试策略")
        print("=" * 100)
        for term in ["原神", "崩坏星穹铁道", "珂莱塔"]:
            best, lens = 0, []
            for i in range(5):
                url = f"https://wapbaike.baidu.com/item/{quote(term)}"
                async with s.get(url) as r:
                    html = (await r.content.read(3 * 1024 * 1024)).decode("utf-8", errors="replace")
                lens.append(len(html))
                # 粗略估计正文：找最长的文本块
                import re as _re
                txts = _re.findall(r">([^<>]{200,})<", html)
                longest = max((len(t) for t in txts), default=0)
                best = max(best, longest)
                await asyncio.sleep(0.4)
            print(f"  {term:<14} HTML大小序列 {lens}  最长文本块 {best}")

        print("\n" + "=" * 100)
        print("【C】openapi 对不同情况的返回")
        print("=" * 100)
        for term, desc in [("依安嘉措", "百科没这个词条"), ("大二普", "可能有"),
                           ("zzz不存在xyz", "完全不存在"), ("崩坏", "有多个义项"),
                           ("上海", "普通词")]:
            st, txt = await openapi(s, term)
            ok, title, desc2, n = try_parse(txt)
            print(f"  {term:<16} ({desc:<14}) HTTP {st} {len(txt):>5}B  "
                  f"{'完整' if ok else '截断'}  title={str(title)[:30]}  desc={str(desc2)[:36]}")
            await asyncio.sleep(0.6)

        print("\n" + "=" * 100)
        print("【D】正式词条名的价值：分词给不出的，openapi 能给")
        print("=" * 100)
        for term in ["珂莱塔", "影神图", "崩坏星穹铁道", "甘雨", "钟离", "刻晴", "夜兰"]:
            st, txt = await openapi(s, term)
            ok, title, desc, n = try_parse(txt)
            flag = "★" if title and title != term else " "
            print(f"  {flag} {term:<12} → 正式词条 {str(title):<28} desc={str(desc)[:34]}")
            await asyncio.sleep(0.6)


if __name__ == "__main__":
    asyncio.run(main())
