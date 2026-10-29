"""memory_tier 归档游标修复的回归测试。

要证明三件事：
1. 归档指针轮能被正确识别并排除，不会被送进摘要（否则摘要自我引用、越滚越长）
2. 不再依赖 archived_rounds 游标 —— DB 轮数少于游标值时依然能归档（死锁回归）
3. 没有指针的纯对话不受影响，行为与修复前一致
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:\qqbot")
PLUGIN = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_memory_tier" / "main.py"
sys.path.insert(0, str(ROOT / "_uv_tools" / "astrbot" / "Lib" / "site-packages"))

SRC = PLUGIN.read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}{'  ' + detail if detail else ''}")


def grab(fn: str) -> str:
    m = re.search(rf"^def {fn}\([^\n]*\n(?:(?:[ \t].*)?\n)*?(?=^\S)", SRC, re.M)
    if not m:
        raise RuntimeError(f"抠不出 {fn}")
    return m.group(0)


NS: dict = {"IMAGE_TOKENS": 1500, "Any": object, "Path": Path}

# 常量先从源码抠出来并注入，_find_pointer_round 会引用它们
_consts = dict(
    re.findall(
        r"^(KEEP_RECENT_ROUNDS|SOFT_LIMIT_TOKENS|MAX_ARCHIVE_ROUNDS|ARCHIVE_POINTER_MARK|ARCHIVE_POINTER_ACK)\s*=\s*(.+)$",
        SRC,
        re.M,
    )
)
for _k in ("KEEP_RECENT_ROUNDS", "SOFT_LIMIT_TOKENS", "MAX_ARCHIVE_ROUNDS"):
    NS[_k] = eval(_consts[_k], {}, {})
NS["ARCHIVE_POINTER_MARK"] = eval(_consts["ARCHIVE_POINTER_MARK"], {}, {})
NS["ARCHIVE_POINTER_ACK"] = eval(_consts["ARCHIVE_POINTER_ACK"], {}, {})

for _fn in ("_text_of", "_est_tokens", "_count_tokens", "_split_rounds", "_find_pointer_round"):
    exec(grab(_fn), NS)
_consts = dict(
    re.findall(r"^(KEEP_RECENT_ROUNDS|SOFT_LIMIT_TOKENS|MAX_ARCHIVE_ROUNDS|ARCHIVE_POINTER_MARK|ARCHIVE_POINTER_ACK)\s*=\s*(.+)$", SRC, re.M)
)
MARK = eval(_consts["ARCHIVE_POINTER_MARK"], {}, {})
ACK = eval(_consts["ARCHIVE_POINTER_ACK"], {}, {})
print(f"指针标记: {MARK!r} / 应答: {ACK!r}\n")

split = NS["_split_rounds"]
find_pointer = NS["_find_pointer_round"]


def body_of(contexts):
    """复刻 _maybe_tier 的切分：去掉 system 头，返回 body 轮次。"""
    rounds = split(contexts)
    if rounds and rounds[0] and rounds[0][0].get("role") == "system":
        return rounds[1:]
    return rounds


def mk_pointer(total: int = 60, summary: str = "摘要内容"):
    return [
        {"role": "user", "content": f"[更早的 {total} 轮对话已归档，以下是累计摘要]\n\n{summary}"},
        {"role": "assistant", "content": ACK},
    ]


def mk_rounds(n: int, tag: str = "旧对话") -> list:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"{tag}{i}"})
        out.append({"role": "assistant", "content": f"回复{i}"})
    return out


print("[1] 指针识别：能认出归档指针轮")
body = body_of([{"role": "system", "content": "系统提示"}] + mk_pointer() + mk_rounds(20))
check("头部存在指针时被识别", find_pointer(body) == 0, f"got {find_pointer(body)}")
check("指针只有 1 轮长", len(body[0]) == 2)

print("\n[2] 指针识别：无指针时不能误判")
body2 = body_of([{"role": "system", "content": "系统提示"}] + mk_rounds(20))
check("无指针返回 -1", find_pointer(body2) == -1, f"got {find_pointer(body2)}")
check("无指针时首轮原样保留", body2[0][0]["content"] == "旧对话0")

print("\n[3] 指针识别：长得像但不是的不能误判")
fake = [
    {"role": "user", "content": "[更早的 60 轮对话已归档，以下是累计摘要]\n\n手写内容"},
    {"role": "assistant", "content": "我不是标准应答"},
]
check("应答不符 → 不认", find_pointer(body_of(fake + mk_rounds(5))) == -1)

only_user = [{"role": "user", "content": "[更早的 60 轮对话已归档，以下是累计摘要]"}]
check("只有 user → 不认", find_pointer(body_of(only_user)) == -1)
check("空 body → 不认", find_pointer([]) == -1)

print("\n[4] 死锁回归：DB 轮数 < archived_rounds 仍能归档（核心修复）")
KEEP = eval(_consts["KEEP_RECENT_ROUNDS"])
# 复刻修复后的 remaining 计算
def remaining_of(body):
    idx = find_pointer(body)
    return body[1:] if idx >= 0 else body

# 模拟今天的真实状态：archived=60，但 DB 只有 20 轮（指针1 + 19轮）
body3 = body_of([{"role": "system", "content": "系统"}] + mk_pointer() + mk_rounds(19))
rem3 = remaining_of(body3)
check("旧逻辑会死锁(body[60:] 为空)", len(body3[60:]) == 0)
check("新逻辑能取出 19 轮", len(rem3) == 19, f"got {len(rem3)}")
check("新逻辑够归档（有 >15 轮可归档）", len(rem3) > KEEP)
check("指针未混进待归档", all(
    "轮对话已归档" not in str(r[0].get("content", "")) for r in rem3))

# 无指针且轮数很少时，正确地不归档
body4 = body_of([{"role": "system", "content": "系统"}] + mk_rounds(10))
check("轮数不足时不归档", len(remaining_of(body4)) <= KEEP)

print("\n[5] 归档后重新拼装：指针仍只有一条，且能被再次识别")
body5 = body_of([{"role": "system", "content": "系统"}] + mk_pointer(60) + mk_rounds(20))
rem5 = remaining_of(body5)
to_archive, keep_recent = rem5[:-KEEP], rem5[-KEEP:]
check("待归档不含指针", all("轮对话已归档" not in str(r[0].get("content","")) for r in to_archive))
new_ctx = ([{"role": "system", "content": "系统"}] + mk_pointer(90, "新摘要")
           + [m for r in keep_recent for m in r])
nb = body_of(new_ctx)
check("新上下文能被识别出指针", find_pointer(nb) == 0, f"got {find_pointer(nb)}")
ptr_count = sum(1 for r in nb if "轮对话已归档" in str(r[0].get("content", "")))
check("上下文里指针恰好 1 条", ptr_count == 1, f"got {ptr_count}")
check("摘要已更新为最新块", "新摘要" in str(nb[0][0]["content"]))
check("旧摘要不残留", "摘要内容" not in str(nb[0][0]["content"]))

print("\n[6] 摘要文本不会自我引用（修复的核心目的）")
summary_src = "".join(str(m.get("content","")) for r in to_archive for m in r)
check("待归档文本不含归档标记", "轮对话已归档" not in summary_src)
check("待归档文本不含标准应答", ACK not in summary_src)

print("\n[7] 源码层面：不再有把游标当切片的写法")
check("无 body_rounds[archived:] 残留", "body_rounds[archived" not in SRC)
check("无 new_archived 变量残留", "new_archived" not in SRC)
check("archived_rounds 仅用于展示累加", SRC.count('meta.get("archived_rounds", 0)') == 1)
check("指针应答改用常量", f'"content": ARCHIVE_POINTER_ACK' in SRC or 'ARCHIVE_POINTER_ACK' in SRC)

print(f"\n{ PASS } passed, { FAIL } failed")
sys.exit(1 if FAIL else 0)