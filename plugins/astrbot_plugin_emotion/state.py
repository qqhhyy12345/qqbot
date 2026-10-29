"""情绪状态的纯函数：衰减、事件更新、限幅。不 import AstrBot，方便单测。

五轴全部按人存：愉悦 p / 唤醒 a / 亲近 c / 疲惫 f / 兴致 i。

存的是**偏离基线的偏差**，不是绝对值。基线里愉悦和亲近分人（主人和路人起点不同），
其余按钟点或常数；衰减只乘偏差，基线读取时再加——钟点基线随时在变，存绝对值
的话历史值全得跟着改。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Axis:
    key: str
    name: str
    h_fast: float  # 分钟
    h_slow: float
    split: float  # 事件里分给快分量的比例
    cap_lo: float  # 慢分量下限
    cap_hi: float


AXES: tuple[Axis, ...] = (
    # 愉悦（旧版"讨厌"取反）：会被一句话扫兴，也会记很久
    Axis("p", "愉悦", 90.0, 48 * 60.0, 0.5, -0.25, 0.25),
    # 唤醒：几十分钟就落，最多撑半天
    Axis("a", "唤醒", 40.0, 8 * 60.0, 0.7, -0.22, 0.22),
    # 亲近：慢变量，一句话只挪一点点，掉了也难补
    Axis("c", "亲近", 36 * 60.0, 20 * 24 * 60.0, 0.3, -0.30, 0.12),
    # 疲惫：一天里攒起来，睡一觉才回去
    Axis("f", "疲惫", 3 * 60.0, 20 * 60.0, 0.5, -0.25, 0.25),
    # 兴致：来得快去得也快
    Axis("i", "兴致", 100.0, 30 * 60.0, 0.7, -0.25, 0.25),
)

KEYS = tuple(a.key for a in AXES)
BY_KEY = {a.key: a for a in AXES}

STEP_CAP = 0.06  # 单事件单轴
ROUND_CAP = 0.10  # 单轮合计
POS_BOOST = 1.5  # 正向事件抵消慢分量时的权重
RESET = 30 * 24 * 60.0  # 一个月没动静就整个人归零

LO, HI = 0.02, 0.98

BASE_C_OWNER = 0.72
BASE_C_OTHER = 0.30
BASE_P_OWNER = 0.85  # 旧版讨厌基线 0.15 取反
BASE_P_OTHER = 0.40
BASE = {"i": 0.55}
BASE_A = ((6, 11, 0.50), (11, 19, 0.55), (19, 23, 0.52))
BASE_F = ((6, 11, 0.22), (11, 19, 0.30), (19, 23, 0.38))
NIGHT_A, NIGHT_F = 0.38, 0.55

# 每句话都留痕：日常闲聊不命中任何规则，也让她动一点点（4 位小数看得见）
BASELINE = {"c": 0.0010, "i": 0.0006, "f": 0.0002}

# 她自己调情绪：单次上限、每天总额度。挡的是"一句话让她把好感拉满"这类注入。
SELF_STEP = 0.05
SELF_STEP_C = 0.02
SELF_DAILY = 0.15
SELF_KEYS = {"愉悦": "p", "唤醒": "a", "亲近": "c", "疲惫": "f", "兴致": "i"}


def self_step(key: str) -> float:
    return SELF_STEP_C if key == "c" else SELF_STEP


def self_allowance(used: float) -> float:
    return max(0.0, SELF_DAILY - used)


def _clock(now: float, table, night: float) -> float:
    h = time.localtime(now).tm_hour
    for lo, hi, v in table:
        if lo <= h < hi:
            return v
    return night


def base_of(key: str, now: float, c0: float = BASE_C_OTHER, p0: float = BASE_P_OTHER) -> float:
    if key == "c":
        return c0
    if key == "p":
        return p0
    if key == "a":
        return _clock(now, BASE_A, NIGHT_A)
    if key == "f":
        return _clock(now, BASE_F, NIGHT_F)
    return BASE.get(key, 0.5)


def fresh_person(now: float, c0: float = BASE_C_OTHER, p0: float = BASE_P_OTHER) -> dict:
    p = {"p0": p0, "c0": c0, "ts": now}
    for k in KEYS:
        p[k + "f"] = 0.0
        p[k + "s"] = 0.0
    return p


def decay(person: dict, now: float) -> dict:
    dt = (now - float(person.get("ts", now))) / 60.0
    c0 = float(person.get("c0", BASE_C_OTHER))
    p0 = float(person.get("p0", BASE_P_OTHER))
    if dt > RESET:
        return fresh_person(now, c0, p0)
    if dt <= 0:  # 时钟回拨：不涨不跌
        return {**person, "ts": now}
    out = {"p0": p0, "c0": c0, "ts": now}
    for ax in AXES:
        vf = float(person.get(ax.key + "f", 0.0))
        vs = float(person.get(ax.key + "s", 0.0))
        out[ax.key + "f"] = vf * 0.5 ** (dt / ax.h_fast)
        out[ax.key + "s"] = max(ax.cap_lo, min(ax.cap_hi, vs * 0.5 ** (dt / ax.h_slow)))
    return out


def apply(
    person: dict, deltas: dict[str, float], now: float, strong: bool = False, w: float = 1.0
) -> dict:
    """普通事件按轴自己的快慢比拆；强事件大部分沉进慢分量（闷下去而不是炸完就散）。

    限幅在**乘权重之前**做：先按原始幅度截到 STEP_CAP / ROUND_CAP，再乘亲疏权重，
    否则权重会被归一化抵消——越亲的人说的话反而没有更重。
    """
    m = decay(person, now)
    total = sum(abs(v) for v in deltas.values())
    scale = min(1.0, ROUND_CAP / total) if total > 0 else 1.0
    for ax in AXES:
        dv = deltas.get(ax.key, 0.0)
        if not dv:
            continue
        dv = max(-STEP_CAP, min(STEP_CAP, dv * scale)) * w
        split = ax.split * (0.25 if strong else 1.0)
        f = dv * split
        s = dv * (1 - split)
        cur = float(m.get(ax.key + "s", 0.0))
        if cur * s < 0:  # 反向事件更容易把人掰回来
            s *= POS_BOOST
        m[ax.key + "f"] = float(m.get(ax.key + "f", 0.0)) + f
        m[ax.key + "s"] = max(ax.cap_lo, min(ax.cap_hi, cur + s))
    return m


def values(person: dict, now: float) -> dict[str, float]:
    c0 = float(person.get("c0", BASE_C_OTHER))
    p0 = float(person.get("p0", BASE_P_OTHER))
    return {
        ax.key: max(
            LO,
            min(HI, base_of(ax.key, now, c0, p0) + float(person.get(ax.key + "f", 0.0)) + float(person.get(ax.key + "s", 0.0))),
        )
        for ax in AXES
    }


def slow_dev(person: dict) -> dict[str, float]:
    return {k: float(person.get(k + "s", 0.0)) for k in KEYS}


def closeness(person: dict, now: float) -> float:
    return values(person, now)["c"]


def weight(person: dict, now: float) -> float:
    """按亲疏加权：越亲的人一句话越重，路人几乎推不动（0.5 ~ 1.5）。"""
    return 0.5 + closeness(person, now)


def saturate(counter: dict, key: str, now: float, window: float = 600.0) -> float:
    """同一个人 10 分钟内第 n 次同向事件：1, 0.67, 0.5, 0.4 …（刷屏很快失效）。"""
    n, last = (counter.get(key) or [0, 0.0])[:2]
    n = int(n) + 1 if now - float(last) <= window else 1
    counter[key] = [n, now]
    return 1.0 / (1.0 + 0.5 * (n - 1))
