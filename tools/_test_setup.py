import json, shutil
from pathlib import Path

ROOT = Path(r"D:\dsh\wkspc1\_test-memory")
if ROOT.exists():
    shutil.rmtree(ROOT)

SESS = ROOT / "memories" / "session" / "t1"
PEOPLE = ROOT / "memories" / "people" / "p1"
SESS.mkdir(parents=True)
PEOPLE.mkdir(parents=True)


def mem(name, desc, typ, body):
    return (
        "---\n"
        f"name: {name[:-3]}\n"
        f"description: {desc}\n"
        "metadata:\n"
        f"  type: {typ}\n"
        "---\n\n"
        f"{body}\n"
    )


files = {
    "user-111.md": ("用户甲的身份", "user", "他是用户甲，做前端。"),
    "feedback-style.md": ("用户甲要求回复简短", "feedback", "回复别超过三句话。"),
    "note-a.md": ("临时安排：周三搬椅子", "project", "周三下午搬椅子。"),
    "note-b.md": ("参考：某篇文章", "reference", "一篇文章的链接说明。"),
    "note-c.md": ("用户甲爱喝美式", "user", "他只喝美式，不加糖。"),
}
for n, (d, t, b) in files.items():
    (SESS / n).write_text(mem(n, d, t, b), encoding="utf-8")
(SESS / "MEMORY.md").write_text("", encoding="utf-8")

(PEOPLE / "user-123.md").write_text(
    mem("user-123.md", "人物档案：用户乙", "user", "用户乙是产品经理。"), encoding="utf-8"
)

# 会话作用域的操作：1 条 feedback 删除（应被保护拒绝）+ 4 条普通删除（受 40% 比例上限约束）
sess_ops = [
    {"op": "delete", "file": "feedback-style.md", "reason": "合并进 user-111.md"},
    {"op": "delete", "file": "user-111.md", "reason": "合并进 merged.md"},
    {"op": "delete", "file": "note-a.md", "reason": "合并进 merged.md"},
    {"op": "delete", "file": "note-b.md", "reason": "合并进 merged.md"},
    {"op": "delete", "file": "note-c.md", "reason": "合并进 merged.md"},
    {
        "op": "write",
        "file": "merged.md",
        "type": "user",
        "description": "测试合并后的档案",
        "content": "合并了用户甲的几条信息。",
        "reason": "合并重复",
    },
]
# 人物作用域：删人物档案（应被拒绝）+ 无写入的纯删除
people_ops = [
    {"op": "delete", "file": "user-123.md", "reason": "合并进别处"},
]

(ROOT / "ops-session.json").write_text(json.dumps(sess_ops, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "ops-people.json").write_text(json.dumps(people_ops, ensure_ascii=False, indent=2), encoding="utf-8")
print("测试数据已建好:", ROOT)
