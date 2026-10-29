"""校验修好后的 _count_tokens：直接从源码里抠函数来跑真实会话数据。"""

import ast
import json
import sqlite3

SRC = r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_tier\main.py"
DB = r"D:\qqbot\astrbot\data\data_v4.db"
IMAGE_TOKENS = 1500
SOFT_LIMIT_TOKENS = 20000

src = open(SRC, encoding="utf-8").read()
tree = ast.parse(src)
wanted = {"_est_tokens", "_count_tokens", "_text_of", "_split_rounds"}
snippets = [
    ast.get_source_segment(src, n)
    for n in tree.body
    if isinstance(n, ast.FunctionDef) and n.name in wanted
]
ns = {"IMAGE_TOKENS": IMAGE_TOKENS, "Any": object}
exec("\n\n".join(snippets), ns)
count_tokens = ns["_count_tokens"]
text_of = ns["_text_of"]
split_rounds = ns["_split_rounds"]

print(f"已载入 {sorted(n for n in ns if n.startswith('_') and n not in ('__', ))}")

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = dict(c.execute("select user_id, content from conversations"))

for umo in (
    "snowluma:FriendMessage:<OWNER_QQ>",
    "snowluma:FriendMessage:<FRIEND_QQ>",
    "snowluma:GroupMessage:<GROUP_ID>",
):
    msgs = json.loads(rows[umo])
    total = count_tokens(msgs)
    old = sum(ns["_est_tokens"](text_of(m)) for m in msgs)
    flag = "会触发归档" if total >= SOFT_LIMIT_TOKENS else "还不够"
    print(f"\n{umo}")
    print(f"  旧口径(只数text) {old:>7,}")
    print(f"  新口径(含think+图) {total:>7,}  → {flag}")
    print(f"  提升 {total - old:+,}（{total/max(old,1):.1f} 倍）")

# 边界用例
cases = [
    ([{"role": "user", "content": "你好"}], "纯字符串 content"),
    ([{"role": "user", "content": [{"type": "text", "text": "abc"}]}], "text part"),
    ([{"role": "assistant", "content": [{"type": "think", "think": "想" * 100}]}], "只有 think"),
    ([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:,"}}]}], "只有图片"),
    ([{"role": "user", "content": None}], "content 为 None"),
    ([], "空上下文"),
]
print("\n边界用例：")
for ctx, name in cases:
    try:
        print(f"  {name:<16} → {count_tokens(ctx):>6}")
    except Exception as exc:
        print(f"  {name:<16} → 异常 {exc}")
