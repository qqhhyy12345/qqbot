"""事件表：正则 → 各轴 Δ。零 token、纯本地。

只认"冲着她说的"话；不认识的人随口一句会被饱和和亲疏权重挡住。
五轴：p 愉悦 / a 唤醒 / c 亲近 / f 疲惫 / i 兴致（愉悦是旧版"讨厌"取反）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    pat: re.Pattern
    delta: dict[str, float]
    strong: bool = False


def _r(name: str, pattern: str, delta: dict[str, float], strong: bool = False) -> Rule:
    return Rule(name, re.compile(pattern), delta, strong)


RULES = (
    _r("abuse", r"(废物|垃圾|你算什么东西|没用的东西|滚开|滚蛋|给我滚)",
       {"p": -0.06, "a": 0.02, "c": -0.012, "f": 0.008, "i": -0.05}, True),
    _r("snub", r"(你好笨|闭嘴|烦死|讨厌你|别烦我|烦不烦|够了吧|蠢死)",
       {"p": -0.05, "a": 0.03, "c": -0.008, "i": -0.04}, True),
    _r("praise", r"(好可爱|厉害|真棒|喜欢你|辛苦|真乖|好聪明|靠谱|谢谢|感谢)",
       {"p": 0.02, "a": 0.01, "c": 0.006, "i": 0.04}),
    _r("cling", r"(抱抱|想你|陪我|在吗|亲亲|贴贴|爱你|mua)",
       {"p": 0.01, "a": 0.01, "c": 0.008, "f": -0.012, "i": 0.03}),
    _r("apology", r"(对不起|抱歉|我错了)",
       {"p": 0.03, "c": 0.004, "f": -0.004, "i": 0.02}),
)

_EMPTY_REPLY = re.compile(
    r"(嗯+|哦+|噢+|喔+|好+的?|好嘞|行|ok|可|中|收到|知道了?|了解|是|对|嗯呐|额+|呃+|啊这|…+|\.+|。+|！+|!+)",
    re.IGNORECASE,
)
_NOISE = re.compile(r"[\s，,。.！!？?~～、]")


def events(text: str, now: float) -> list[tuple[str, dict[str, float], bool]]:
    """返回 (名字, {轴: Δ}, 是否强事件)。规则各命中一次，不重复计。"""
    t = (text or "").strip()
    out: list[tuple[str, dict[str, float], bool]] = []
    for r in RULES:
        if r.pat.search(t):
            out.append((r.name, dict(r.delta), r.strong))
    if not t:
        return out

    # 敷衍只在"没别的事"时才计：一句"你好笨"是被怼，不该同时算敷衍
    if len(t) >= 60 and ("？" in t or "?" in t or "「" in t or "“" in t):
        out.append(("long", {"a": 0.008, "c": 0.003, "f": 0.004, "i": 0.015}, False))
    if not out and _EMPTY_REPLY.fullmatch(_NOISE.sub("", t)):
        out.append(("perfunctory", {"p": -0.01, "a": -0.005, "c": -0.002, "i": -0.02}, False))
    return out
