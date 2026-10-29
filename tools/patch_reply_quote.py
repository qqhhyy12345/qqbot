"""给 AstrBot 打个补丁：引用回复只在群聊生效，私聊不显示。

## 起因

`platform_settings.reply_with_quote` 开启后，**私聊回复也会带上引用**，
因为 AstrBot 自己的判断是不对称的：

    # result_decorate/stage.py
    if (
        self.reply_with_mention
        and event.get_message_type() != MessageType.FRIEND_MESSAGE   # ← 有判断
    ):
        result.chain.insert(0, At(...))

    if self.reply_with_quote:                                       # ← 没判断
        result.chain.insert(0, Reply(...))

`reply_with_mention` 会跳过私聊，`reply_with_quote` 不会。这是上游的疏漏。

## 为什么不能靠插件解决

查过管线时序后确认：**没有任何插件钩子能在 Reply 被插入之后再动手**。

| 时机 | 位置 | Reply 组件是否已插入 |
|---|---|---|
| `on_llm_response` / `on_agent_done` | LLM 返回时 | 否 |
| `on_decorating_result` 钩子 | stage.py:160 | 否 |
| Reply 插入 | stage.py:447 | — |
| `RespondStage` 发送 | stage.py:194 | 是 |

三个钩子全部早于插入点。插件层拦不住，只能改核心代码。

## 代价与应对

改的是 `_uv_tools/astrbot/Lib/site-packages/` 里的文件，
`pip install -U astrbot` 会被覆盖。所以：
- 本脚本随时可 `undo` 撤销
- 用**精确字符串匹配**而不是行号，升级后原逻辑没变也能重新 apply
- 匹配不上就报错退出，绝不盲改

用法：
    python patch_reply_quote.py status   # 看当前状态
    python patch_reply_quote.py apply    # 打补丁
    python patch_reply_quote.py undo     # 撤销
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

TARGET = Path(
    r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages"
    r"\astrbot\core\pipeline\result_decorate\stage.py"
)
BACKUP = TARGET.with_name("stage.py.orig")

ORIGINAL = """                if self.reply_with_quote:
                    result.chain.insert(0, Reply(id=event.message_obj.message_id))"""

PATCHED = """                if (
                    self.reply_with_quote
                    and event.get_message_type() != MessageType.FRIEND_MESSAGE
                ):
                    result.chain.insert(0, Reply(id=event.message_obj.message_id))"""


def status() -> int:
    if not TARGET.exists():
        print(f"[FAIL] 目标文件不存在: {TARGET}")
        return 1
    text = TARGET.read_text(encoding="utf-8")
    has_patched = PATCHED in text
    has_original = ORIGINAL in text
    print(f"文件: {TARGET}")
    print(f"备份: {BACKUP}{'  (存在)' if BACKUP.exists() else '  (无)'}")
    if has_patched:
        print("状态: 已打补丁 —— 引用回复只在群聊生效")
        return 0
    if has_original:
        print("状态: 原始 —— 引用回复私聊也会生效")
        return 0
    print("状态: 未知（两段都匹配不到）")
    print("      AstrBot 版本可能变了，或补丁被改过。需要人工核对。")
    return 1


def apply() -> int:
    if not TARGET.exists():
        print(f"[FAIL] 目标文件不存在: {TARGET}")
        return 1
    text = TARGET.read_text(encoding="utf-8")

    if PATCHED in text:
        print("[SKIP] 已经是打过补丁的状态，无需重复应用")
        return 0
    if ORIGINAL not in text:
        print("[FAIL] 匹配不到原始代码块，已中止，未做任何修改。")
        print("       可能原因：AstrBot 版本变化，或该段被人手改过。")
        print("       请人工检查：")
        for i, line in enumerate(text.splitlines(), 1):
            if "reply_with_quote" in line:
                print(f"         {i}: {line}")
        return 1

    # 先留一份 .orig，便于人工比对
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"已备份原文件 → {BACKUP.name}")

    if text.count(ORIGINAL) != 1:
        print(f"[FAIL] 原始代码块出现 {text.count(ORIGINAL)} 次，不敢盲改")
        return 1

    TARGET.write_text(text.replace(ORIGINAL, PATCHED), encoding="utf-8")
    print("[OK] 已打补丁：引用回复现在只在群聊生效")
    return 0


def undo() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if PATCHED not in text:
        print("[SKIP] 当前不是补丁状态，无需撤销")
        return 0
    TARGET.write_text(text.replace(PATCHED, ORIGINAL), encoding="utf-8")
    print("[OK] 已撤销，恢复 AstrBot 原始行为")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"status": status, "apply": apply, "undo": undo}.get(cmd)
    if fn is None:
        print("用法: python patch_reply_quote.py [status|apply|undo]")
        sys.exit(1)
    sys.exit(fn())