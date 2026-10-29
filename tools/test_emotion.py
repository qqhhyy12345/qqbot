"""情绪插件纯逻辑测试（不需要起 AstrBot）。

python tools/test_emotion.py
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "astrbot" / "data" / "plugins" / "astrbot_plugin_emotion"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_emo_{name}", PLUGIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


state = _load("state")
signals = _load("signals")
label = _load("label")

T0 = datetime(2026, 10, 29, 15, 0, 0).timestamp()  # 下午：a 基线 0.55，f 基线 0.30
HOUR = 3600.0
ok = 0


def check(name: str, got, want, tol: float = 1e-3) -> None:
    global ok
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        good = abs(got - want) <= tol
    else:
        good = got == want
    print(f"{'✓' if good else '✗'} {name}: {got}" + ("" if good else f"  期望 {want}"))
    ok += bool(good)


OWNER = state.fresh_person(T0, state.BASE_C_OWNER, state.BASE_P_OWNER)
OTHER = state.fresh_person(T0)


def vals(person, now=T0) -> dict:
    return state.values(state.decay(person, now), now)


def one(text: str, person=None, now=T0, w: float | None = None):
    """按 main 的顺序跑一条消息：留痕 + 每个命中的事件（带饱和）。"""
    person = state.decay(person or OWNER, now)
    w = state.weight(person, now) if w is None else w
    person = state.apply(person, state.BASELINE, now, w=w)
    sat: dict = {}
    for name, delta, strong in signals.events(text, now):
        mult = state.saturate(sat, name, now)
        person = state.apply(person, delta, now, strong, w=w * mult)
    return person


# ---- 基线与起点
check("a 基线(下午)", state.base_of("a", T0), 0.55)
check("a 基线(凌晨3点)", state.base_of("a", T0 - 12 * HOUR), 0.38)
check("f 基线(下午)", state.base_of("f", T0), 0.30)
check("f 基线(凌晨3点)", state.base_of("f", T0 - 12 * HOUR), 0.55)
check("愉悦基线(主人)", state.base_of("p", T0, state.BASE_C_OWNER, state.BASE_P_OWNER), 0.85)
check("愉悦基线(路人)", state.base_of("p", T0), 0.40)
check("亲近起点(主人)", vals(OWNER)["c"], 0.72)
check("亲近起点(路人)", vals(OTHER)["c"], 0.30)
check("五轴齐全", sorted(vals(OWNER)), ["a", "c", "f", "i", "p"])

# ---- 亲疏加权：0.5 ~ 1.5
check("主人权重", state.weight(OWNER, T0), 1.22)
check("路人权重", state.weight(OTHER, T0), 0.80)

# ---- 每句留痕：闲聊也推一点点，4 位小数看得见
before = vals(OWNER)["c"]
after = vals(one("今天天气不错", OWNER))["c"]
check("闲聊也涨亲近", after - before, 0.00122, tol=1e-5)
check("闲聊不动愉悦", vals(one("今天天气不错", OTHER))["p"], 0.40, tol=1e-6)
check("路人闲聊涨得更少", vals(one("今天天气不错", OTHER))["c"] - 0.30, 0.0008, tol=1e-5)

# ---- 强事件：快慢比例 0.25 倍速（原 0.5 → 0.125）
m = state.apply(state.fresh_person(T0), {"p": 0.04}, T0, strong=True)
check("强事件 快分量", m["pf"], 0.005)
check("强事件 慢分量", m["ps"], 0.035)
m = state.apply(state.fresh_person(T0), {"p": 0.04}, T0, strong=False)
check("普通事件 快分量", m["pf"], 0.020)
check("普通事件 慢分量", m["ps"], 0.020)

# ---- 衰减：各轴自己的半衰期
m = {"c0": 0.3, "p0": 0.4, "ts": T0, "ps": -0.04, "cs": 0.08}
m.update({k + "f": 0.0 for k in state.KEYS})
check("愉悦慢分量 48h 半衰", state.decay(m, T0 + 48 * HOUR)["ps"], -0.02)
check("亲近慢分量 20 天半衰", state.decay(m, T0 + 20 * 24 * HOUR)["cs"], 0.04)
check("一个月没动静归零", state.decay(m, T0 + 31 * 24 * HOUR)["cs"], 0.0)
check("时钟回拨不涨", state.decay(m, T0 - HOUR)["ps"], -0.04)

# ---- 限幅：单事件 0.06、慢分量上限
m = state.fresh_person(T0)
for _ in range(6):
    m = state.apply(m, {"c": 0.20}, T0)
check("亲近慢分量封顶 +0.12", m["cs"], 0.12)
m = state.fresh_person(T0)
for _ in range(10):
    m = state.apply(m, {"p": -0.20}, T0)
check("愉悦慢分量下限 -0.25", m["ps"], -0.25)

# ---- 正向事件抵消慢分量 ×1.5
m = state.fresh_person(T0)
m["ps"] = -0.05
m = state.apply(m, {"p": 0.02}, T0)
check("正向抵消有加成", m["ps"], -0.035, tol=1e-6)

# ---- 她自己调情绪：单次上限 / 每日额度 / 不吃亲疏权重
check("自调单次上限(愉悦)", state.self_step("p"), 0.05)
check("自调单次上限(亲近)", state.self_step("c"), 0.02)
check("自调每日额度", state.self_allowance(0.0), 0.15)
check("自调额度用完", state.self_allowance(0.15), 0.0)
check("中文轴名可解析", state.SELF_KEYS["亲近"], "c")
m = state.apply(OWNER, {"p": 0.05}, T0, w=1.0)
check("自调不吃亲疏权重", vals(m)["p"], 0.90, tol=1e-4)
m = state.apply(OTHER, {"p": 0.05}, T0, w=1.0)
check("路人自调幅度一样", vals(m)["p"], 0.45, tol=1e-4)

# ---- 同向饱和
sat = {}
t = T0
check("第1次", state.saturate(sat, "k", t), 1.0)
check("第2次", state.saturate(sat, "k", t + 30), 0.6667)
check("第3次", state.saturate(sat, "k", t + 60), 0.5, tol=1e-4)
check("隔 11 分钟重置", state.saturate(sat, "k", t + 60 + 700), 1.0)

# ---- 事件识别
def names(text: str) -> list[str]:
    return [e[0] for e in signals.events(text, T0)]


check("被怼", names("你好笨啊"), ["snub"])
check("侮辱", names("你就是个废物"), ["abuse"])
check("被夸", names("你好可爱"), ["praise"])
check("撒娇", names("抱抱"), ["cling"])
check("道歉", names("对不起"), ["apology"])
check("敷衍", names("嗯"), ["perfunctory"])
check("敷衍-好的宝宝", names("好的宝宝"), [])
check("我爱你不是敷衍", names("我爱你"), ["cling"])
check("我好累不是敷衍", names("我好累"), [])
check("短问句不算敷衍", names("这是谁"), [])
check("表情不算敷衍", names("⊙∀⊙！"), [])
LONG_TEXT = (
    "你是不是觉得这个方案有问题？我总觉得哪里不太对劲，要不再仔细想想，"
    "别急着上线，明天上午我们再一起看一遍，把时间点和风险点都列出来"
)
check("认真长文", names(LONG_TEXT), ["long"])
check("空白消息不误判", names("   "), [])

# ---- 事件方向
snub = one("你好笨啊", OWNER)
check("被怼掉愉悦", vals(snub)["p"] < 0.85, True)
check("被怼掉亲近", vals(snub)["c"] < 0.72, True)
check("被怼掉兴致", vals(snub)["i"] < 0.55, True)
praise = one("厉害", OWNER)
check("被夸涨兴致", vals(praise)["i"] > 0.55, True)
check("被夸涨亲近", vals(praise)["c"] > 0.72, True)
check("被夸不降愉悦", vals(praise)["p"] >= 0.85, True)

# ---- 场景：被怼一次当场 / 隔夜
m = one("你好笨啊", OWNER)
check("被怼当场愉悦", vals(m)["p"], 0.8023, tol=0.003)
check("被怼当场唤醒", vals(m)["a"], 0.5786, tol=0.003)
check("被怼隔夜还有余味", vals(m, T0 + 8 * HOUR)["p"] < 0.83, True)
check("被怼 3 天后基本过去", vals(m, T0 + 72 * HOUR)["p"] > 0.83, True)

# ---- 场景：路人骂她，分量明显更轻
loud = "你就是个废物"
check("路人被骂掉得少", 0.40 - vals(one(loud, OTHER))["p"] < 0.85 - vals(one(loud, OWNER))["p"], True)

# ---- 人话
v = vals(OWNER)
block = label.describe(v, state.slow_dev(OWNER))
check("文案含亲近档位", "挺熟" in block, True)
check("文案带反表演约束", "别演它" in block, True)
check("刻度 4 位小数", "亲近 0.7200" in block, True)
check("五轴都上刻度", all(f"{label.NAME[k]} " in block for k in state.KEYS), True)
check("无残留时不提记着", "记着" in block, False)
check("慢分量触发残留", "还有点记着" in label.describe(v, {"p": -0.05}), True)
check("大残留说得更重", "这事还没过去" in label.describe(v, {"p": -0.12}), True)
check("低档不念档位", label.axis_text("p", 0.2), "跟他待着没什么意思")

print(f"\n{ok} 项通过" if ok else "\n全挂")
