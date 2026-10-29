"""算账 v2：把 think / 图片也算进去，看 memory_tier 实际省多少、理论上能省多少。

插件自带的 _count_tokens 只数 content 里 type=="text" 的片段，
think（推理痕迹）和 image_url 全被忽略 —— 这直接影响它是否触发归档。
"""

import json
import sqlite3
from pathlib import Path

DB = r"D:\qqbot\astrbot\data\data_v4.db"
ARCH = Path(r"D:\qqbot\bot-memory\archive")
KEEP = 15
SOFT = 20000
MAX_ARC = 30

IMG_TOKENS = 1500  # 一张 JPEG 按 volcengine 视觉口径粗估


def est(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 0.7 + (len(text) - cjk) * 0.3)


def parts(msg):
    ct = msg.get("content")
    if isinstance(ct, str):
        return [("text", {"text": ct})]
    out = []
    if isinstance(ct, list):
        for p in ct:
            if isinstance(p, dict):
                out.append((p.get("type"), p))
    return out


def tok_text_only(msg) -> int:
    """插件口径：只数 text"""
    return sum(est(str(p.get("text") or "")) for t, p in parts(msg) if t == "text")


def tok_real(msg) -> int:
    """真实口径：text + think + 图片"""
    n = 0
    for t, p in parts(msg):
        if t == "text":
            n += est(str(p.get("text") or ""))
        elif t == "think":
            n += est(str(p.get("think") or p.get("text") or ""))
        elif t == "image_url":
            n += IMG_TOKENS
    return n


def rounds_of(msgs):
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


def safe(umo):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in umo)


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = dict(c.execute("select user_id, content from conversations"))

    targets = [
        ("linwan 私聊", "snowluma:FriendMessage:<OWNER_QQ>"),
        ("私聊 3201680698", "snowluma:FriendMessage:3201680698"),
        ("群 <GROUP_ID>", "snowluma:GroupMessage:<GROUP_ID>"),
    ]

    for label, umo in targets:
        raw = rows.get(umo)
        if not raw:
            continue
        head, rounds = rounds_of(json.loads(raw))

        full = sum(tok_real(m) for r in rounds for m in r)
        naive = sum(tok_text_only(m) for r in rounds for m in r)
        think = sum(
            est(str(p.get("think") or ""))
            for r in rounds for m in r for t, p in parts(m) if t == "think"
        )
        imgs = sum(1 for r in rounds for m in r for t, p in parts(m) if t == "image_url")

        md = ARCH / f"{safe(umo)}.md"
        meta = ARCH / f"{safe(umo)}.meta.json"
        sum_tok = est(md.read_text(encoding="utf-8")) if md.exists() else 0
        archived = (
            int(json.loads(meta.read_text(encoding="utf-8")).get("archived_rounds") or 0)
            if meta.exists() else 0
        )

        body = rounds[archived:]
        keep = body[-KEEP:]
        keep_tok = sum(tok_real(m) for r in keep for m in r)

        # 插件真实行为：拿 naive 计数去比 20000
        would_run = len(body) >= 30 and naive >= SOFT
        # 如果计数口径正确，会不会跑
        would_run_fixed = len(body) >= 30 and full >= SOFT

        after_real = (sum(tok_real(m) for m in head) + sum_tok + keep_tok) if would_run else full
        after_fixed = (sum(tok_real(m) for m in head) + sum_tok + keep_tok) if would_run_fixed else full

        print(f"\n===== {label} =====")
        print(f"  轮数 {len(rounds)}   已归档 {archived} 轮   摘要 {sum_tok} token")
        print(f"  插件口径(只数text)  {naive:>7,} token  → {'≥' if naive>=SOFT else '<'} 20000，"
              f"{'会' if would_run else '不会'}归档")
        print(f"  真实口径(含think)   {full:>7,} token  （其中 think {think:,}、图片 {imgs} 张）")
        print(f"  最近 {KEEP} 轮         {keep_tok:>7,} token")
        print(f"  当前实际每请求      {after_real:>7,} token")
        print(f"  口径修好后每请求    {after_fixed:>7,} token，省 {full-after_fixed:,} "
              f"({(full-after_fixed)/full*100:.1f}%)")


if __name__ == "__main__":
    main()
