"""把旧分裂群会话的摘要合并进共享群会话，并把旧行改名留档（可逆）。

前置：AstrBot 必须已停止，否则内存缓存会覆盖写入。
"""

import json
import sqlite3
from pathlib import Path

DB = r"D:\qqbot\astrbot\data\data_v4.db"
SUM = Path(r"D:\qqbot\bot-memory\archive\_legacy_group_split\summaries.json")
SHARED = "snowluma:GroupMessage:<GROUP_ID>"
ARCHIVE_PREFIX = "_archived:"


def text_of(msg) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def est(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 0.7 + (len(text) - cjk) * 0.3)


def main():
    sums = json.loads(SUM.read_text(encoding="utf-8"))
    c = sqlite3.connect(DB)
    cur = c.cursor()

    cur.execute("select content from conversations where user_id=?", (SHARED,))
    row = cur.fetchone()
    if not row:
        print(f"找不到共享会话 {SHARED}")
        return
    old_raw = row[0]
    old_msgs = json.loads(old_raw)

    # 已经合并过就别重复插
    if any("群里各位之前分别聊过" in text_of(m) for m in old_msgs[:3]):
        print("检测到已合并过，跳过插入")
    else:
        blocks = ["[群里各位之前分别聊过的内容已合并成一份记忆，以下是要点]"]
        for umo, r in sums.items():
            blocks.append(f"\n## 和{r['who']}\n{r['summary'].strip()}")
        pointer_text = "\n".join(blocks)

        pointer = [
            {"role": "user", "content": [{"type": "text", "text": pointer_text}]},
            {"role": "assistant", "content": [{"type": "text", "text": "好，记着了。"}]},
        ]
        new_msgs = pointer + old_msgs
        cur.execute(
            "update conversations set content=?, updated_at=datetime('now') where user_id=?",
            (json.dumps(new_msgs, ensure_ascii=False), SHARED),
        )
        print(f"已插入回顾块：{len(pointer_text)} 字 ≈ {est(pointer_text)} token")
        print(f"共享会话消息数 {len(old_msgs)} -> {len(new_msgs)}")

    # 旧行改名留档（不动内容、不删数据）
    renamed = 0
    for umo in sums:
        cur.execute(
            "update conversations set user_id=? where user_id=? and user_id not like ?",
            (ARCHIVE_PREFIX + umo, umo, ARCHIVE_PREFIX + "%"),
        )
        renamed += cur.rowcount
    c.commit()
    print(f"旧分裂会话已改名为 {ARCHIVE_PREFIX}… 共 {renamed} 行")

    cur.execute("select user_id, length(content), token_usage from conversations order by updated_at desc")
    print("\n合并后各会话：")
    for u, ln, tk in cur.fetchall():
        print(f"  {u:<52} {ln:>8,} 字符  {tk}")
    c.close()


if __name__ == "__main__":
    main()
