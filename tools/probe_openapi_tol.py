"""第十三轮：openapi 的输入容忍度 —— 决定插件形态。

已确认：
  - 百科站内搜索全被风控（「验证_百度百科」页）
  - openapi 100% 稳定，能给正式词条名 + 结构化 card
  - `site:baike.baidu.com <单个词>` 有效；加第二个词就崩

要测：
  A. openapi 对**长句**（用户原话）会怎样？
     如果 openapi 直接吃长句就能命中，那插件根本不需要分词！
  B. openapi 对**分词候选词**的反应
  C. openapi 能否拿到正文摘要（bk_length 的真实含义）
  D. 三个入口组合的端到端价值
"""
import asyncio
import json
import re
import sys
from urllib.parse import quote

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def openapi(s, term, length=600):
    url = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
           f"&format=json&appid=379020&bk_key={quote(term)}&bk_length={length}")
    async with s.get(url) as r:
        raw = await r.content.read(3 * 1024 * 1024)
    txt = raw.decode("utf-8", errors="replace")
    try:
        obj, _ = json.JSONDecoder().raw_decode(txt)
        return obj, len(txt)
    except Exception:
        return None, len(txt)


def show(obj, size, tag):
    if not obj:
        print(f"    {tag:<26} 未命中（{size}B）")
        return
    title = obj.get("title")
    desc = obj.get("desc")
    card = obj.get("card") or []
    print(f"    {tag:<26} title={str(title)[:26]:<28} desc={str(desc)[:30]:<32} card={len(card)}")
    # 看看有没有"简介"这类长文本字段
    for c in card:
        nm = str(c.get("name"))
        v = c.get("value")
        if isinstance(v, list):
            vs = " / ".join(str(x) for x in v)
        else:
            vs = str(v)
        if any(k in nm for k in ("简介", "概述", "内容", "剧情", "描述", "介绍")) or len(vs) > 120:
            print(f"         ▸ {nm[:20]:<22} {vs[:150]}")


async def main():
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        print("=" * 104)
        print("【A】openapi 直接吃长句 —— 关键测试")
        print("=" * 104)
        long_qs = [
            "崩坏星穹铁道2.0版本新角色有哪些",
            "原神里甘雨这个角色到底怎么培养",
            "鸣潮珂莱塔这个角色怎么抽到需要什么材料",
            "崩坏星穹铁道",
            "珂莱塔",
            "影神图",
        ]
        for q in long_qs:
            obj, size = await openapi(s, q)
            print(f"\n  输入: {q}")
            show(obj, size, "原样")
            await asyncio.sleep(0.7)

        print("\n" + "=" * 104)
        print("【B】openapi 对分词候选词")
        print("=" * 104)
        for q, cands in [
            ("崩坏星穹铁道2.0版本新角色有哪些", ["崩坏星穹铁道", "星穹铁道", "崩坏"]),
            ("鸣潮珂莱塔这个角色怎么抽到需要什么材料", ["鸣潮珂莱塔", "鸣潮", "珂莱塔"]),
            ("原神里甘雨这个角色到底怎么培养", ["原神", "甘雨"]),
        ]:
            print(f"\n  原查询: {q}")
            for c in cands:
                obj, size = await openapi(s, c)
                show(obj, size, c)
                await asyncio.sleep(0.6)

        print("\n" + "=" * 104)
        print("【C】bk_length 的真实含义（能不能拿到更长正文）")
        print("=" * 104)
        for ln in [100, 600, 2000, 10000]:
            obj, size = await openapi(s, "崩坏星穹铁道", ln)
            print(f"\n  bk_length={ln}")
            show(obj, size, f"len={ln}")
            await asyncio.sleep(0.6)

        print("\n" + "=" * 104)
        print("【D】openapi 的 desc 是不是完整简介（对比必应摘要）")
        print("=" * 104)
        for t in ["崩坏星穹铁道", "珂莱塔", "原神", "影神图"]:
            obj, _ = await openapi(s, t)
            if obj:
                print(f"\n  {t}")
                print(f"    desc 长度: {len(str(obj.get('desc')))}")
                print(f"    desc: {str(obj.get('desc'))[:200]}")
            await asyncio.sleep(0.6)


if __name__ == "__main__":
    asyncio.run(main())
