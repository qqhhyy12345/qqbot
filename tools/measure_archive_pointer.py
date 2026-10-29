"""实测归档指针（累计摘要）在上下文里的 token 占比。

背景：memory_tier 每次归档都把**整个归档文件**拼进请求的指针块，
而归档文件只增不减（私聊已 25 个块 / 15,105 字节）。

用法：<AstrBot 的 python> tools/measure_archive_pointer.py
"""
import asyncio
import io
import json
import sys
from pathlib import Path

import aiohttp

CFG = json.load(io.open(r"D:\qqbot\astrbot\data\cmd_config.json", encoding="utf-8-sig"))
SRC = next(p for p in CFG["provider_sources"] if "ark" in str(p.get("api_base", "")))
KEY = SRC["key"][0] if isinstance(SRC["key"], list) else SRC["key"]
BASE = SRC["api_base"].rstrip("/")
MODEL = next(p for p in CFG["provider"] if "ark" in str(p.get("id", "")))["model"]

ARCHIVE_DIR = Path(r"D:\qqbot\bot-memory\archive")


async def count_tokens(session, text: str) -> int:
    async with session.post(
        f"{BASE}/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 1,
        },
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=aiohttp.ClientTimeout(total=90),
    ) as r:
        return (await r.json()).get("usage", {}).get("prompt_tokens", 0)


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        base = await count_tokens(s, "x")
        print(f"基线（1 字符）: {base} token\n")
        print(f"{'归档文件':44s} {'字节':>7s} {'token':>7s} {'块数':>5s}")
        print("-" * 70)
        for md in sorted(ARCHIVE_DIR.glob("*.md")):
            text = md.read_text(encoding="utf-8").strip()
            if not text:
                continue
            blocks = text.count("## 归档块")
            tok = (await count_tokens(s, text)) - base
            print(f"{md.name:44s} {len(text):7d} {tok:7d} {blocks:5d}")


if __name__ == "__main__":
    asyncio.run(main())
