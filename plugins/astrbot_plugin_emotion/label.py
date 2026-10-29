"""把五轴连续值说成人话，末行附内部刻度（她自己对表用，别念数字）。"""

from __future__ import annotations

BANDS = (0.25, 0.42, 0.58, 0.75)

P = ("跟他待着没什么意思", "就那样吧", "", "挺开心，愿意多聊", "跟他在一起很开心")
A = ("没精神，话会少", "有点犯困，句子短一些", "", "挺有精神，愿意多说", "很兴奋，语气跳一点")
C = ("还生分，客气些", "算认识了", "算熟了", "挺熟，可以松一点", "很熟，用平时的称呼")
F = ("不累", "", "有点累", "挺累的，别招她", "累坏了，没什么耐心")
I = ("提不起劲", "不太想聊", "", "想聊，愿意接话", "兴致高，话多")

TEXT = {"p": P, "a": A, "c": C, "f": F, "i": I}
NAME = {"p": "愉悦", "a": "唤醒", "c": "亲近", "f": "疲惫", "i": "兴致"}
ORDER = ("p", "a", "c", "f", "i")


def band(x: float) -> int:
    for i, b in enumerate(BANDS):
        if x < b:
            return i
    return len(BANDS)


def axis_text(key: str, value: float) -> str:
    return TEXT[key][band(value)]


def slow_text(key: str, slow: float) -> str:
    return _slow_line({key: slow})


def _slow_line(slow: dict[str, float]) -> str:
    """残留通道：档位看不出来的小幅余味靠这句露出来，顺序按"最压人的那个"来。"""
    if slow.get("p", 0.0) <= -0.10:
        return "这事还没过去，语气别太轻快"
    if slow.get("p", 0.0) <= -0.04:
        return "还有点记着"
    if slow.get("i", 0.0) <= -0.10:
        return "提不起劲"
    if slow.get("i", 0.0) >= 0.10:
        return "还在高兴头上"
    if slow.get("f", 0.0) >= 0.10:
        return "累得没什么耐心"
    if slow.get("c", 0.0) <= -0.06:
        return "跟这个人还没缓过来"
    return ""


def head_line(vals: dict[str, float]) -> str:
    return "，".join(x for x in (axis_text(k, vals.get(k, 0.5)) for k in ORDER) if x)


def residual_line(slow: dict[str, float]) -> str:
    return _slow_line(slow)


def gauge_line(vals: dict[str, float]) -> str:
    return "【内部刻度】" + " / ".join(f"{NAME[k]} {vals.get(k, 0.0):.4f}" for k in ORDER)


def describe(vals: dict[str, float], slow: dict[str, float]) -> str:
    lines = ["# 你此刻的状态", head_line(vals), gauge_line(vals)]
    tail = _slow_line(slow)
    if tail:
        lines.append(tail)
    lines.append("（只影响语气，别提它，也别演它；数字自己看，别念出来。）")
    return "\n".join(lines)
