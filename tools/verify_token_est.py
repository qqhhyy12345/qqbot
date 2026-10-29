"""用真实会话数据校验新的 token 估算到底准不准。

对照基准：`conversations.token_usage` = 上一次 LLM 响应返回的**真实** usage。
这是唯一可信的真值来源，估算只能向它看齐。
"""

import json
import sqlite3
import sys
from pathlib import Path

PLUGIN_DIR = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_tier")
sys.path.insert(0, str(PLUGIN_DIR))
import main as mt  # noqa: E402

DB = r"D:\qqbot\astrbot\data\data_v4.db"


class _FakeConv:
    def __init__(self, usage):
        self.token_usage = usage


class _FakeReq:
    def __init__(self, contexts, usage):
        self.contexts = contexts
        self.conversation = _FakeConv(usage)
        self.system_prompt = ""
        self.func_tool = None
        self.extra_user_content_parts = []


c = sqlite3.connect(DB)
rows = c.execute(
    """SELECT user_id, content, token_usage, updated_at FROM conversations
       WHERE token_usage > 0 ORDER BY updated_at DESC"""
).fetchall()

print("=" * 96)
print(f"{'会话':40s} {'真实值':>8s} {'旧估算':>8s} {'偏差':>7s} {'新估算':>8s} {'偏差':>7s}")
print("=" * 96)

for uid, content, real, upd in rows:
    try:
        ctxs = json.loads(content)
    except Exception:  # noqa: BLE001
        continue
    if not isinstance(ctxs, list) or not ctxs:
        continue

    # 旧口径：CJK 0.7 / 其他 0.3，且不算固定开销
    old = 0
    for m in ctxs:
        ct = m.get("content")
        if isinstance(ct, str):
            cjk = sum(1 for ch in ct if "\u4e00" <= ch <= "\u9fff")
            old += int(cjk * 0.7 + (len(ct) - cjk) * 0.3)
            continue
        if not isinstance(ct, list):
            continue
        for p in ct:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                t = str(p.get("text") or "")
                cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
                old += int(cjk * 0.7 + (len(t) - cjk) * 0.3)
            elif p.get("type") == "think":
                t = str(p.get("think") or "")
                cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
                old += int(cjk * 0.7 + (len(t) - cjk) * 0.3)
            elif p.get("type") == "image_url":
                old += 1500

    new = mt._estimate_request_tokens(_FakeReq(ctxs, real), ctxs)
    o_dev = (old - real) / real * 100
    n_dev = (new - real) / real * 100
    print(f"{uid[-38:]:40s} {real:8d} {old:8d} {o_dev:+6.1f}% {new:8d} {n_dev:+6.1f}%")

print()
print("说明：新估算 = max(真实值, 估算值)，所以偏差只会 >= 0（绝不会低估）。")
print("     偏差为 0 表示估算低于真实值，此时采信真实值 —— 这正是设计意图。")