"""wake_gate 唤醒闸门的回归测试。

样本取自真实群聊记录（群 <GROUP_ID>，2026-10-23 下午），
覆盖那几类真实的"没喊她也回"的现场。

核心要证明：
1. 群里 @ 了别人、没 @ 她 → 判为"没在叫她"
2. 四种放行情形各自能正确识别（@本人 / @全体 / 引用我 / 唤醒词）
3. 判定逻辑与 AstrBot 自身语义一致（唤醒词只看开头）
4. 私聊、群白名单、开关、异常兜底都不会误伤
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:\qqbot")
PLUGIN = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_wake_gate" / "main.py"
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


# ---- 造组件替身：只提供 _is_addressing_me 真正用到的那几个属性 ----
class At:
    def __init__(self, qq):
        self.qq = qq


class AtAll:
    pass


class Reply:
    def __init__(self, sender_id):
        self.sender_id = sender_id


class Plain:
    def __init__(self, text):
        self.text = text


def grab(fn: str) -> str:
    # 签名可能跨多行，不能用"到下一个非缩进行"来截断。
    # 改用：从 def 行开始，一直取到下一个顶层 def/class 为止。
    m = re.search(rf"^def {fn}\(.*?(?=^def |^class |\Z)", SRC, re.M | re.S)
    if not m:
        raise RuntimeError(f"抠不出 {fn}")
    return m.group(0)


NS: dict = {"At": At, "AtAll": AtAll, "Reply": Reply}
exec(grab("_is_addressing_me"), NS)
exec(grab("_is_at_me"), NS)
gate = NS["_is_addressing_me"]
at_gate = NS["_is_at_me"]

# 常量从源码抠，避免手抄漂移
WAKE = eval(
    re.search(r"^WAKE_WORDS: list\[str\] = (.+)$", SRC, re.M).group(1), {}, {}
)
NICK = eval(
    re.search(r"^SELF_NICKNAMES: list\[str\] = (.+)$", SRC, re.M).group(1), {}, {}
)

BOT = "<BOT_QQ>"      # <BOT_NICK>自己的号
ME = "<OWNER_QQ>"       # 主人
OTHER = "<MEMBER_QQ1>"    # 群友 群友昵称
THIRD = "<FRIEND_QQ>"    # 群友 梵蒂冈御史大夫

print(f"机器人自号 {BOT} / 唤醒词 {WAKE}\n")

print("[1] 真实群聊样本：没喊她 → 应该掐断")
cases_nowake = [
    ("@ 别人（某群友）", [Plain("[发送者：QQ名 群友昵称？！｜QQ号 <MEMBER_QQ1>]"),
                      At(THIRD), Plain(" 嗯，那是我自己一行一行改的，所以读着像我")]),
    ("@ 别人（某群友）", [Plain("[发送者：QQ名 群友昵称？！｜QQ号 <MEMBER_QQ2>]"),
                       At(ME), Plain(" 踏马的我用的都是手机上的免费的")]),
    ("纯闲聊，没人@", [Plain("[发送者：QQ名 群友昵称？！｜QQ号 <MEMBER_QQ1>]"),
                    Plain("我见过的坑：一次改一大片，改完自己都认不出自己")]),
    ("@ 两个人但没她", [At(THIRD), Plain(" "), At(ME)]),
]
for name, msgs in cases_nowake:
    ok, why = gate(msgs, BOT, WAKE)
    check(f"{name} → 掐断", ok is False, f"got {ok} ({why})")

print("\n[2] 放行情形：@ 本人")
for name, msgs in [
    ("裸 @", [At(BOT)]),
    ("@ 在文字中间", [Plain("看看这个"), At(BOT), Plain(" 怎么办")]),
    ("@ 数字型 str", [At(str(BOT))]),
]:
    ok, why = gate(msgs, BOT, WAKE)
    check(f"{name} → 放行", ok is True, f"got {ok} ({why})")

print("\n[3] 放行情形：@ 全体 / 引用我")
ok, why = gate([AtAll(), Plain(" 大家看看")], BOT, WAKE)
check("@全体(AtAll形态) → 放行", ok is True, f"got {ok} ({why})")
# 真实形态：aiocqhttp 把 @全体 解析成 At(qq="all")，不是 AtAll
ok, why = gate([At(qq="all"), Plain(" 大家看看")], BOT, WAKE)
check("@全体(At(all)形态·真实) → 放行", ok is True, f"got {ok} ({why})")
ok, why = gate([Reply(BOT), Plain(" 你说话按钮还在吗")], BOT, WAKE)
check("引用我 → 放行", ok is True, f"got {ok} ({why})")
ok, _ = gate([Reply(OTHER), Plain(" 说点啥")], BOT, WAKE)
check("引用别人 → 掐断", ok is False)

print("\n[3b] @ 全体绝不能被当成 @ 别人掐断（回归）")
for label, comp in [("AtAll", AtAll()), ("At(all)", At(qq="all"))]:
    ok, why = gate([comp, Plain(" 在吗")], BOT, WAKE)
    check(f"{label} → 放行（不能误杀）", ok is True, f"got {ok} ({why})")

print("\n[3d] @昵称 纯文本（真实根因回归）")
# 来源：astrbot/data/logs/astrbot.log 18:11:47 那条被误掐的记录
NICK = ["<BOT_NICK>"]
# QQ 上按昵称 at 不生成 At 组件，只发纯文本 "@<BOT_NICK>"
msgs = [Plain("@<BOT_NICK> 我又来啦，这回听得见吗")]
ok, why = gate(msgs, BOT, WAKE, NICK)
check("@<BOT_NICK> 纯文本 → 放行（真实根因）", ok is True, f"got {ok} ({why})")
# 反例：正文里提到昵称但不在开头，不能误判
msgs2 = [Plain("刚才<BOT_NICK>说的那个问题我想了想")]
ok, _ = gate(msgs2, BOT, WAKE, NICK)
check("昵称在正文中 → 不算（防误伤）", ok is False)
# 反例：@别人昵称
msgs3 = [Plain("@鲸鲸 你再试试")]
ok, _ = gate(msgs3, BOT, WAKE, NICK)
check("@别人的昵称 → 掐断", ok is False)
# 未传昵称参数时仍要放行 —— 靠的是 WAKE_WORDS 里那条 "@<BOT_NICK>"。
# 这条断言原先写成 "got is False"，是错的：WAKE_WORDS 含 "@<BOT_NICK>"
# （与 cmd_config.json 的 wake_prefix 保持一致），所以不传昵称参数也认。
ok, why = gate(msgs, BOT, WAKE)
check("不传昵称参数 → 仍放行（靠 wake_prefix 里的 @昵称）", ok is True, f"got {ok} ({why})")
check("且依据是唤醒词而非昵称兜底", "唤醒词" in why, f"got {why}")
# 顺带钉住这个一致性：WAKE_WORDS 必须含 "@昵称"，否则上游唤醒阶段就先掐了
check("WAKE_WORDS 含 @昵称（与 cmd_config 的 wake_prefix 对齐）",
      "@" + NICK[0] in WAKE, f"got {WAKE}")

print("\n[3c] self_id 为空时，@ 的判定不可信 → 主流程已放行")
ok, why = gate([At(qq=BOT)], "", WAKE)
check("空 self_id 下 @机器人 判不出（主流程会提前 return 放行）", ok is False, f"got {ok}")
check("源码含 self_id 空值保护", "if not self_id:" in SRC)

print("\n[4] 放行情形：唤醒词（只看开头，跟 AstrBot 一致）")
for w in WAKE:
    ok, why = gate([Plain(f"{w} 你在吗")], BOT, WAKE)
    check(f"唤醒词 {w!r} 开头 → 放行", ok is True, f"got {ok} ({why})")
ok, _ = gate([Plain("你在吗 <BOT_NICK>"), Plain("顺便问一下")], BOT, WAKE)
check("唤醒词在中间 → 不算（与 AstrBot 一致）", ok is False)
ok, _ = gate([Plain("鲸鱼是什么")], BOT, WAKE)
check("“鲸鱼”开头会被唤醒（已知的宽松点）", ok is True)

print("\n[5] 边界：@ 自己不等于 @ 机器人")
ok, _ = gate([At(ME), Plain(" 我问你")], BOT, WAKE)
check("@主人本人 → 掐断", ok is False)

print("\n[6] 边界：空消息 / 异常组件不崩")
for name, msgs in [("空列表", []), ("全 None", [None, Plain("")]), ("无 text 属性", [object()])]:
    try:
        ok, why = gate(msgs, BOT, WAKE)
        check(f"{name} → 不抛异常", True)
        check(f"{name} → 判为未唤醒", ok is False, f"got {ok}")
    except Exception as e:
        check(f"{name} → 不抛异常", False, str(e))

print("\n[7] 源码层面：关键设计点")
check("只挂 on_waiting_llm_request", "@filter.on_waiting_llm_request()" in SRC)
check("掐断用 stop_event", "event.stop_event()" in SRC)
check("私聊直接放行", "if event.is_private_chat():" in SRC and "return" in SRC)
check("整体包 try 兜底", "except Exception as exc" in SRC)
check("未调用 event.send", "event.send" not in SRC, "不应主动发消息")
check("只引用必要组件", SRC.count("from astrbot.core.message.components import") == 1)

print("\n[8] 被 @ 硬保证：@ 命中时必须放行，且优先于一切其他判定")
# 真正触发「模型判定语句不重要」的样本：@ 了她，但内容极短/像随口一句
cases_at = [
    ("@本人 + 一句话", [At(BOT), Plain(" 在吗")], True),
    ("@本人 + 单字", [At(BOT), Plain("？")], True),
    ("@本人 + 纯表情式", [At(BOT), Plain(" 嗯")], True),
    ("@全体", [AtAll(), Plain(" 有人吗")], True),
    ("aiocqhttp 的 At(all)", [At("all"), Plain(" 看下这个")], True),
    # QQ 按昵称 at 时不生成 At 组件，只在消息最前面放一段纯文本
    # （真实消息前面没有别的东西；日志里那句「[发送者：...]」是渲染加的）
    ("@昵称（纯文本兜底）", [Plain("@<BOT_NICK> 在吗")], True),
    ("@昵称 + 后跟内容", [Plain("@<BOT_NICK> 帮我看下这个")], True),
    ("@昵称 超出 12 字窗口 → 不算", [Plain("喂喂喂<BOT_NICK>在不在啊")], False),
    ("引用她", [Reply(BOT), Plain(" 这条怎么说")], True),
    ("@别人 → 不算被 @", [At(THIRD), Plain(" 在吗")], False),
    ("只有昵称出现在正文中间 → 不算", [Plain(" 我刚才问过<BOT_NICK>了")], False),
]
for name, msgs, want in cases_at:
    got, why = at_gate(msgs, BOT, NICK)
    check(f"{name} → {want}", got is want, f"got {got} ({why})")

# 优先级：@ 命中时，唤醒词判定无关紧要 —— 且不得被「不叫醒」覆盖
ok_at, why_at = at_gate([At(BOT), Plain("")], BOT, NICK)
check("@本人 时无条件为真", ok_at is True, f"got {ok_at}")
check("@本人 时依据是 @ 而非唤醒词", "唤醒词" not in why_at, f"got {why_at}")

print("\n[9] 强制回复指令：内容与注入方式")
import ast as _ast

_hint = None
for _n in _ast.parse(SRC).body:
    if isinstance(_n, _ast.Assign) and any(
        isinstance(_t, _ast.Name) and _t.id == "AT_FORCE_REPLY_HINT"
        for _t in _n.targets
    ):
        _hint = _ast.literal_eval(_n.value)
hint = _hint or ""
check("指令要求实质回复", "必须给出实质回复" in hint, f"got {hint[:60]}")
check("指令禁止内心旁白", "内心旁白" in hint)
check("指令禁止沉默", "沉默" in hint)
check("指令覆盖「内容很短也回」", "很短" in hint and "不重要" in hint)

print("\n[10] 源码层面：注入实现的三条硬约束")
check("用 TextPart 而不是 dict（dict 会被 isinstance 检查打断请求）",
      "TextPart(text=AT_FORCE_REPLY_HINT)" in SRC and
      'extra_user_content_parts.append({"type"' not in SRC)
check("标记 mark_as_temp 防持久化", "mark_as_temp()" in SRC)
check("从 event extra 取 provider_request",
      'get_extra("provider_request")' in SRC)
check("拿不到 req 时只放行不报错", "return False" in SRC)
check("_is_at_me 分支在 _is_addressing_me 之前（硬保证优先）",
      SRC.index("at_me, at_reason = _is_at_me") < SRC.index("addressed, reason = _is_addressing_me"))
# 结构上：@ 分支里注入完就 return，物理上走不到 stop_event。
# 注意不能拿 SRC.index("event.stop_event()") 比——那个字符串首次出现在模块 docstring 里。
check("@ 分支注入后立即 return，走不到掐断",
      "_inject_force_reply(event, at_reason)\n                return" in SRC)
check("掐断代码只有一处（_is_at_me 之前不可能触达）",
      SRC.count("            event.stop_event()") == 1)

print(f"\n{ PASS } passed, { FAIL } failed")
sys.exit(1 if FAIL else 0)