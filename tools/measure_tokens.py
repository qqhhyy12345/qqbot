"""精确测量工具输出的 token 数（用方舟 API 的 usage 返回）。

拿真实的工具输出文本，发一次最小请求（max_tokens=1），读 usage.prompt_tokens。
这比按字符数猜准得多——中文 1 汉字到底算几个 token，只有模型自己知道。

用法：<AstrBot 的 python> tools/measure_tokens.py
"""

import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp

CFG = json.load(
    io.open(r"D:\qqbot\astrbot\data\cmd_config.json", encoding="utf-8-sig")
)
SRC = CFG["provider_sources"][0]
KEY = SRC["key"]
if isinstance(KEY, list):
    KEY = KEY[0]
BASE = SRC["api_base"].rstrip("/")
MODEL = CFG["provider"][0]["model"]


async def count_tokens(session, label: str, text: str) -> int:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 1,
        "stream": False,
    }
    async with session.post(
        f"{BASE}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        raw = await resp.text()
    if resp.status >= 400:
        print(f"  {label}: 请求失败 {resp.status} {raw[:200]}")
        return -1
    obj = json.loads(raw)
    usage = obj.get("usage") or {}
    pt = usage.get("prompt_tokens", -1)
    ct = usage.get("completion_tokens", -1)
    print(f"  {label}")
    print(f"    字符数 {len(text):6d} → prompt_tokens {pt:6d} "
          f"(每字符 {pt / max(len(text), 1):.3f} token)  输出 {ct}")
    return pt


async def main():
    # ① 日志里真实那次 baike_lookup 输出（三花猫）
    baike_out = Path(r"D:\qqbot\tools\_tmp_baike_out.txt")
    samples = []
    if baike_out.exists():
        t = baike_out.read_text(encoding="utf-8")
        # 截到日志噪音之前
        for marker in ("\n[2026-", "\nPrepare to send"):
            if marker in t:
                t = t.split(marker)[0]
        samples.append(("baike_lookup 实测(三花猫)", t.strip()))

    # ② 典型 search_web 输出（实跑一次）
    sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")
    from astrbot_plugin_fetch_url import main as _m  # noqa: E402

    print("=" * 70)
    print(f"模型：{MODEL}    地址：{BASE}")
    print("=" * 70)
    async with aiohttp.ClientSession() as session:
        for label, text in samples:
            await count_tokens(session, label, text)

        # ③ 拿 search_web 的真实输出规模
        try:
            from astrbot_plugin_fetch_url import baike as _b
            import jieba

            jieba.setLogLevel(60)
            # 百科通道典型输出
            text, _ = await _b.baike_lookup(
                "珂莱塔", jieba_mod=jieba, include_body=True
            )
            await count_tokens(session, "baike_lookup(珂莱塔·含正文)", text)
        except Exception as exc:  # noqa: BLE001
            print(f"  baike_lookup 采样失败: {exc}")

        # ④ 对照组：纯中文 1000 字
        await count_tokens(session, "对照组·纯中文 1000 字", "测" * 1000)


asyncio.run(main())
