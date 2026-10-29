"""情绪状态 —— 她对每个人的五轴状态，每轮以一句人话注入。

三条硬约束（改动前先看）：
1. 注入**不写进 system_prompt**，拼在 contexts 末尾（历史之后）。写进 system_prompt 等于
   每轮改提示词前缀，缓存全失效，实测有效成本 ×4.33。
2. 注入以话为主，末行附一行内部刻度（她要看数字，但写明别念出来）。
3. 任何异常一律放行，绝不影响正常回复。

五轴（愉悦/唤醒/亲近/疲惫/兴致）全部按人存：同一个群里，每个人一套。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from . import label, signals, state

PLUGIN_NAME = "astrbot_plugin_emotion"
OWNER_ID = "<OWNER_QQ>"
SAT_KEEP = 24 * 3600.0  # 饱和计数的有效期（10 分钟窗口之外都算废数据）
DATA = Path(__file__).resolve().parents[2] / "plugin_data" / PLUGIN_NAME / "state.json"


def _anchor(uid: str) -> tuple[float, float]:
    """(亲近基线, 愉悦基线)：主人和路人的起点不一样。"""
    if uid == OWNER_ID:
        return state.BASE_C_OWNER, state.BASE_P_OWNER
    return state.BASE_C_OTHER, state.BASE_P_OTHER


class EmotionPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._cache: dict | None = None
        self._mtime: float | None = None
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/page/state", self.page_state, ["GET"], "情绪状态面板数据"
        )

    def _load(self) -> dict:
        try:
            mtime: float | None = DATA.stat().st_mtime
        except OSError:
            mtime = None
        if self._cache is not None and mtime == self._mtime:
            return self._cache
        data = {"person": {}, "sat": {}, "selfbudget": {}}
        if mtime is not None:
            try:
                raw = json.loads(DATA.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("version") == 3:
                    for k in data:
                        if isinstance(raw.get(k), dict):
                            data[k] = raw[k]
                else:  # 旧版格式（v1 会话心情 / v2 讨厌轴）跟现在对不上，留着备份重启
                    logger.warning(f"[{PLUGIN_NAME}] 旧版状态不再兼容，改名为 .old.bak 后按空状态起步")
                    with contextlib.suppress(OSError):
                        DATA.rename(DATA.with_suffix(".old.bak"))
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 状态文件读不出（按空状态起步）: {exc}")
                with contextlib.suppress(OSError):
                    DATA.rename(DATA.with_suffix(".bak"))
        self._cache, self._mtime = data, mtime
        return data

    def _person(self, data: dict, uid: str, now: float) -> dict:
        return data["person"].get(uid) or state.fresh_person(now, *_anchor(uid))

    def _prune(self, data: dict, now: float) -> None:
        """饱和计数 1 天没动就没用了；人一个月没说话就整套归零。"""
        for key, val in list(data["sat"].items()):
            if not isinstance(val, list) or len(val) < 2 or now - float(val[1]) > SAT_KEEP:
                data["sat"].pop(key, None)
        for key, person in list(data["person"].items()):
            if not isinstance(person, dict) or now - float(person.get("ts", 0)) > state.RESET * 60:
                data["person"].pop(key, None)
                data["selfbudget"].pop(key, None)

    def _save(self, data: dict) -> None:
        try:
            self._prune(data, time.time())
            DATA.parent.mkdir(parents=True, exist_ok=True)
            tmp = DATA.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 3, **data}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, DATA)
            self._cache, self._mtime = data, DATA.stat().st_mtime
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 状态写盘失败（忽略）: {exc}")

    def _advance(self, event: AstrMessageEvent) -> str:
        now = time.time()
        uid = str(event.get_sender_id() or "")
        text = getattr(event, "message_str", "") or ""

        data = self._load()
        person = state.decay(self._person(data, uid, now), now)
        w = state.weight(person, now)

        # 每句都留痕：闲聊不命中规则也推一点点
        person = state.apply(person, state.BASELINE, now, w=w)

        fired = []
        for name, delta, strong in signals.events(text, now):
            mult = state.saturate(data["sat"], f"{uid}|{name}", now)
            if mult < 0.34:  # 同向刷屏到第四遍基本无效
                continue
            person = state.apply(person, delta, now, strong, w=w * mult)
            fired.append(f"{name}x{mult:.2f}")

        data["person"][uid] = person
        self._save(data)

        vals = state.values(person, now)
        block = label.describe(vals, state.slow_dev(person))
        logger.debug(
            f"[{PLUGIN_NAME}] {uid[-10:]} w={w:.2f} "
            + " ".join(f"{k}={v:.3f}" for k, v in vals.items())
            + (f" | {','.join(fired)}" if fired else "")
        )
        return block

    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req) -> None:
        try:
            block = self._advance(event)
            if req is not None and block:
                # 复制一份再追加，避免把这条系统消息带进历史
                req.contexts = list(req.contexts or []) + [{"role": "system", "content": block}]
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 处理失败（已放行）: {exc}")

    async def page_state(self) -> dict:
        """面板数据。只读，不改状态；异常也返回结构化的错误，别让页面白屏。"""
        try:
            now = time.time()
            data = self._load()
            persons = []
            for uid, raw in data["person"].items():
                last = float(raw.get("ts", 0))  # decay 会把 ts 改成 now，先取
                person = state.decay(raw, now)
                vals = state.values(person, now)
                slow = state.slow_dev(person)
                c0 = float(raw.get("c0", state.BASE_C_OTHER))
                p0 = float(raw.get("p0", state.BASE_P_OTHER))
                persons.append(
                    {
                        "uid": uid,
                        "owner": uid == OWNER_ID,
                        "vals": {k: round(v, 4) for k, v in vals.items()},
                        "base": {k: round(state.base_of(k, now, c0, p0), 4) for k in state.KEYS},
                        "slow": {k: round(v, 4) for k, v in slow.items()},
                        "anchor": c0,
                        "p_anchor": p0,
                        "weight": round(state.weight(person, now), 3),
                        "text": label.head_line(vals),
                        "residual": label.residual_line(slow),
                        "gauge": label.gauge_line(vals),
                        "last": last,
                    }
                )
            persons.sort(key=lambda p: (p["owner"], p["last"]), reverse=True)
            caps = {a.key: [a.cap_lo, a.cap_hi] for a in state.AXES}
            return {
                "status": "ok",
                "now": now,
                "axes": list(state.KEYS),
                "names": label.NAME,
                "caps": caps,
                "persons": persons,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 面板取数失败: {exc}")
            return {"status": "error", "message": str(exc)}

    @filter.llm_tool(name="adjust_mood")
    async def adjust_mood(
        self, event: AstrMessageEvent, axis: str, amount: float, reason: str = ""
    ) -> str:
        """改你自己此刻的情绪、或者对眼前这个人的态度。只在对话真的让你心情变了的时候用——被冷落、被暖到、聊得不想停、被烦到了。别用它表演情绪，也别每条消息都改。

        改动很小：单次最多 0.05（亲近最多 0.02），每天总共只有 0.15 的额度，用完就改不动了。改完会告诉你新值和剩余额度。

        Args:
            axis(string): 五个之一：愉悦、唤醒、亲近、疲惫、兴致
            amount(number): 改动量，正数=涨、负数=降，比如 0.03 或 -0.02
            reason(string): 一句话原因，写给自己看，不会说给对方
        """
        try:
            key = state.SELF_KEYS.get((axis or "").strip())
            if key is None:
                return "未改（axis 只能是 愉悦/唤醒/亲近/疲惫/兴致）"
            try:
                delta = float(amount)
            except (TypeError, ValueError):
                return "未改（amount 要是数字）"
            if not delta:
                return "未改（改动量是 0）"

            now = time.time()
            uid = str(event.get_sender_id() or "")
            data = self._load()
            day, used, left = self._budget(data, uid, now)
            if left <= 0:
                return "未改（今天的自调额度用完了）"

            step = state.self_step(key)
            delta = max(-step, min(step, delta))
            delta = max(-left, min(left, delta))

            person = state.decay(self._person(data, uid, now), now)
            before = state.values(person, now)[key]
            person = state.apply(person, {key: delta}, now, w=1.0)
            after = state.values(person, now)[key]

            data["person"][uid] = person
            data["selfbudget"][uid] = [day, used + abs(delta)]
            self._save(data)
            note = (reason or "").strip()
            logger.info(
                f"[{PLUGIN_NAME}] 自调 {label.NAME[key]} {delta:+.3f} → {after:.4f}"
                + (f"｜{note[:40]}" if note else "")
            )
            return (
                f"已调整：{label.NAME[key]} {before:.4f}→{after:.4f}"
                f"（今天还能自调 {state.self_allowance(used + abs(delta)):.2f}）"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 自调失败（已放行）: {exc}")
            return "未改（内部出错，按原样继续）"

    def _budget(self, data: dict, uid: str, now: float) -> tuple[str, float, float]:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        tag, used = (data["selfbudget"].get(uid) or [day, 0.0])[:2]
        used = float(used) if tag == day else 0.0
        return day, used, state.self_allowance(used)

    @filter.command("情绪")
    async def show(self, event: AstrMessageEvent):
        # AstrBot 的 PermissionType.ADMIN 靠 event.role，aiocqhttp 从没赋过值，永远是 member
        if str(event.get_sender_id() or "") != OWNER_ID:
            return
        now = time.time()
        data = self._load()
        uid = str(event.get_sender_id() or "")
        person = state.decay(self._person(data, uid, now), now)
        vals = state.values(person, now)
        slow = state.slow_dev(person)
        tail = " ".join(f"{label.NAME[k]}{v:+.3f}" for k, v in slow.items())
        yield event.plain_result(
            label.describe(vals, slow) + f"\n权重 {state.weight(person, now):.2f}｜慢分量 {tail}"
        )
