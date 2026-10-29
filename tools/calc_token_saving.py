"""算账：memory_tier 每轮到底省多少 token。

口径与插件一致（cjk*0.7 + 其他*0.3），这样和它自己的阈值判断对得上。
"""

import json
import sqlite3
from pathlib import Path

DB = r"D:\qqbot\astrbot\data\data_v4.db"
ARCH = Path(r"D:\qqbot\bot-memory\archive")

KEEP_RECENT_ROUNDS = 15


def est(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 0.7 + (len(text) - cjk) * 0.3)


def text_of(msg) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            str(p.get("text", "")) for p in c
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def rounds_of(msgs):
    """按 user 消息切轮次，system 头单独拎出来。"""
    head, rounds, cur = [], [], []
    for m in msgs:
        role = m.get("role")
        if role == "system":
            head.append(m)
            continue
        if role == "user":
            if cur:
                rounds.append(cur)
            cur = [m]
        else:
            cur.append(m)
    if cur:
        rounds.append(cur)
    return head, rounds


def safe(umo: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in umo)


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = dict(c.execute("select user_id, content from conversations"))

    targets = [
        ("linwan 私聊", "snowluma:FriendMessage:<OWNER_QQ>"),
        ("私聊 3201680698", "snowluma:FriendMessage:3201680698"),
        ("群 144808575", "snowluma:GroupMessage:144808575"),
    ]

    print(f"{'会话':<18}{'轮数':>5}{'全量token':>10}{'现在发送':>10}{'每轮省':>9}{'节省':>8}")
    print("-" * 66)

    for label, umo in targets:
        raw = rows.get(umo)
        if not raw:
            continue
        head, rounds = rounds_of(json.loads(raw))

        full_tok = sum(est(text_of(m)) for r in rounds for m in r)
        head_tok = sum(est(text_of(m)) for m in head)

        # 归档摘要（若已有）
        md = ARCH / f"{safe(umo)}.md"
        meta = ARCH / f"{safe(umo)}.meta.json"
        sum_tok = 0
        archived = 0
        if md.exists() and meta.exists():
            sum_tok = est(md.read_text(encoding="utf-8"))
            archived = int(json.loads(meta.read_text(encoding="utf-8")).get("archived_rounds") or 0)

        body = rounds[archived:]
        keep = body[-KEEP_RECENT_ROUNDS:]
        keep_tok = sum(est(text_of(m)) for r in keep for m in r)

        # 关键：插件只在「轮数够 30 且上下文超 20k」时才真正改写请求，
        # 不满足条件时一个 token 都不动，不能按"保留15轮"去算。
        triggered = len(body) >= 30 and (head_tok + sum_tok + keep_tok) >= 20000
        if triggered:
            now_tok = head_tok + sum_tok + keep_tok
        else:
            now_tok = head_tok + full_tok

        saved = full_tok - now_tok
        per_round = saved / len(rounds) if rounds else 0
        state = "已触发" if triggered else "未触发"

        print(f"{label:<18}{len(rounds):>5}{full_tok:>10,}{now_tok:>10,}"
              f"{per_round:>9,.0f}{saved/full_tok*100 if full_tok else 0:>7.1f}%  {state}")


if __name__ == "__main__":
    main()
