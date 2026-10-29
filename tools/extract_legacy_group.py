"""把 5 条旧的分裂群会话抽取成纯文本，统计体量（只读，不改库）。"""

import json
import sqlite3
from pathlib import Path

DB = r"D:\qqbot\astrbot\data\data_v4.db"
LEGACY = [
    "snowluma:GroupMessage:<OWNER_QQ>_144808575",
    "snowluma:GroupMessage:3576388696_144808575",
    "snowluma:GroupMessage:3825551887_144808575",
    "snowluma:GroupMessage:2501413156_144808575",
]
SHARED = "snowluma:GroupMessage:144808575"


def text_of(msg) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return ""


def transcript(raw: str) -> str:
    try:
        msgs = json.loads(raw)
    except Exception as e:
        return f"(解析失败: {e})"
    lines = []
    for m in msgs:
        role = m.get("role")
        t = text_of(m).strip()
        if not t:
            continue
        tag = {"user": "用户", "assistant": "<BOT_NICK>", "system": "系统"}.get(role, role or "?")
        lines.append(f"[{tag}] {t}")
    return "\n".join(lines)


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = c.cursor()
    cur.execute("select user_id, content from conversations")
    rows = {u: ct for u, ct in cur.fetchall()}

    total = 0
    print(f"{'会话':<48}{'消息数':>7}{'文本字符':>10}{'估token':>9}")
    for umo in LEGACY + [SHARED]:
        raw = rows.get(umo)
        if not raw:
            print(f"{umo:<48}  (不存在)")
            continue
        msgs = json.loads(raw)
        txt = transcript(raw)
        cjk = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
        est = int(cjk * 0.7 + (len(txt) - cjk) * 0.3)
        total += est if umo != SHARED else 0
        print(f"{umo:<48}{len(msgs):>7}{len(txt):>10,}{est:>9,}")

    print(f"\n5 条旧会话合计约 {total:,} token（共享那条不算）")
    d = Path(r"D:\qqbot\bot-memory\archive\_legacy_group_split")
    d.mkdir(parents=True, exist_ok=True)
    for umo in LEGACY:
        raw = rows.get(umo)
        if raw:
            name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in umo)
            (d / f"{name}.json").write_text(raw, encoding="utf-8")
            (d / f"{name}.txt").write_text(transcript(raw), encoding="utf-8")
    print(f"已导出到 {d}")


if __name__ == "__main__":
    main()
