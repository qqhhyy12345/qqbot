"""实测「归档合并压缩」的真实效果。

用真实 provider（方舟 API）把现有归档全文合并成一个总摘要，
再和「最近 3 块」拼成指针，实测 token 并与合并前对比。

**只读 + 临时目录**，不碰真实归档文件。

用法：<AstrBot 的 python> tools/measure_merge_effect.py
"""
import asyncio
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import aiohttp

PLUGIN_DIR = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_tier")
sys.path.insert(0, str(PLUGIN_DIR))
import main as mt  # noqa: E402

CFG = json.load(io.open(r"D:\qqbot\astrbot\data\cmd_config.json", encoding="utf-8-sig"))
SRC = next(p for p in CFG["provider_sources"] if "ark" in str(p.get("api_base", "")))
KEY = SRC["key"][0] if isinstance(SRC["key"], list) else SRC["key"]
BASE = SRC["api_base"].rstrip("/")
MODEL = next(p for p in CFG["provider"] if "ark" in str(p.get("id", "")))["model"]

REAL_ARCHIVE = Path(r"D:\qqbot\bot-memory\archive")


class ArkResp:
    def __init__(self, text):
        self.completion_text = text


class ArkProvider:
    """最小 provider 替身，只实现 text_chat。"""

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    async def text_chat(self, contexts=None, request_max_retries=1, **kw):
        self.calls += 1
        prompt = contexts[0]["content"]
        self.prompts.append(prompt)
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{BASE}/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    # 不传 max_tokens —— 与 AstrBot 的行为一致（源码 #9206 默认省略），
                    # 否则推理模型的 token 全被 reasoning 吃掉、content 为空
                    "max_tokens": 8192,
                },
                headers={"Authorization": f"Bearer {KEY}"},
                timeout=aiohttp.ClientTimeout(total=180),
            ) as r:
                data = await r.json()
        return ArkResp(data["choices"][0]["message"]["content"])


async def count_tokens(text: str) -> int:
    async with aiohttp.ClientSession() as s:
        async with s.post(
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


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="merge_effect_"))
    orig_dir = mt._archive_dir
    mt._archive_dir = lambda: tmp
    try:
        for md in REAL_ARCHIVE.glob("*.md"):
            shutil.copy2(md, tmp / md.name)

        prov = ArkProvider()
        plugin = mt.MemoryTierPlugin(type("Ctx", (), {
            "get_using_provider": lambda self, umo=None: prov,
        })())

        print(f"{'会话':40s} {'合并前':>9s} {'合并后':>9s} {'降幅':>8s}")
        print("-" * 72)
        for md in sorted(tmp.glob("*.md")):
            name = md.stem
            old_text = md.read_text(encoding="utf-8").strip()
            if not old_text:
                continue
            blocks = [b for b in old_text.split("## 归档块 ") if b.strip()]

            # 模拟一次新归档（用最后一块当"新摘要"，实际会是新生成的摘要）
            new_summary = blocks[-1].split("\n\n", 1)[-1] if blocks else "（空）"

            merged = await plugin._merge_summaries(None, old_text, new_summary)
            recent = plugin._read_recent_blocks.__get__(plugin)(None, 0) \
                if False else ""
            # 手工拼：总摘要 + 最近 N 块
            recent_n = mt.KEEP_RECENT_SUMMARY_BLOCKS
            parts = [b for b in blocks[-recent_n:]]
            recent_txt = "\n\n".join("## 归档块 " + p.strip() for p in parts)
            pointer = merged + "\n\n" + recent_txt

            t_old = await count_tokens(old_text)
            t_new = await count_tokens(pointer)
            print(f"{name[:38]:40s} {t_old:9d} {t_new:9d} {(1 - t_new / t_old) * 100:7.1f}%")
            print(f"    总摘要 {len(merged)} 字 / {await count_tokens(merged)} token"
                  f"；最近 {recent_n} 块 {len(recent_txt)} 字")
            print(f"    合并调用消耗的输入：{await count_tokens(prov.prompts[-1])} token")
    finally:
        mt._archive_dir = orig_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
