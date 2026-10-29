"""按真实聊天记录回放，生成情绪插件的初始 state.json（v2：五轴按人）。

只读 snowluma 的 messages.db（WAL，必须用 mode=ro URI），按时间顺序把每句话喂给
astrbot_plugin_emotion 的 state/signals：闲聊留痕 + 命中规则的强事件。

    python tools/init_emotion_state.py [起始时间 YYYY-MM-DD] [结束时间 YYYY-MM-DD]

群消息只回放"真的叫她"的那些（@她 / @全体 / 引用她 / @哈鲸鲸 / 唤醒词开头），
与 astrbot_plugin_wake_gate 的判定一致——没叫她的群聊她根本没进 LLM，不该记账。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_emotion"
OUT = ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_emotion" / "state.json"
DB = next((ROOT / "snowluma" / "data").glob("*/messages.db"))

OWNER_ID = "<OWNER_QQ>"
WAKE_WORDS = ("/", "哈鲸鲸", "人机", "鲸", "@哈鲸鲸")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_emo_{name}", PLUGIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


state = _load("state")
signals = _load("signals")
label = _load("label")


def addressed(parts: dict, self_id: str, reply_author: dict) -> bool:
    for p in parts["comps"]:
        if p["type"] == "at" and str(p.get("qq")) in (self_id, "all"):
            return True
        if p["type"] == "reply" and reply_author.get(str(p.get("id"))) == self_id:
            return True
    head = parts["text"].lstrip()[:12]
    if head.startswith("@哈鲸鲸"):
        return True
    return any(w and parts["text"].startswith(w) for w in WAKE_WORDS)


def main() -> None:
    start = datetime.strptime(sys.argv[1], "%Y-%m-%d") if len(sys.argv) > 1 else (
        datetime.now() - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.strptime(sys.argv[2], "%Y-%m-%d") if len(sys.argv) > 2 else datetime.now() + timedelta(days=1)
    t0, t1 = start.timestamp(), end.timestamp()

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        "select is_group, session_id, timestamp, data from messages "
        "where timestamp >= ? and timestamp < ? order by timestamp", (int(t0), int(t1))
    ).fetchall()

    # 引用要回查被引用的那条是谁发的（她自己的消息 id 是负数）
    reply_author: dict[str, str] = {}
    for _, _, _, data in con.execute("select is_group, session_id, timestamp, data from messages"):
        try:
            d = json.loads(data or "{}")
        except ValueError:
            continue
        mid, uid = d.get("message_id"), d.get("user_id")
        if mid is not None and uid is not None:
            reply_author[str(mid)] = str(uid)

    self_id = next((str(json.loads(d).get("self_id")) for _, _, _, d in
                    con.execute("select is_group, session_id, timestamp, data from messages limit 1")), "")

    person: dict[str, dict] = {}
    sat: dict[str, list] = {}
    stats: list[dict] = []
    skipped_group = 0
    replayed = 0

    for is_group, sid, ts, data in rows:
        try:
            d = json.loads(data or "{}")
        except ValueError:
            continue
        uid = str(d.get("user_id", ""))
        if not uid or uid == self_id:
            continue
        comps = [c.get("data", {}) | {"type": c.get("type")}
                 for c in (d.get("message") or []) if isinstance(c, dict)]
        text = "".join(str(c.get("text", "")) for c in comps if c.get("type") == "text")
        if not text.strip():
            continue
        if is_group and not addressed({"comps": comps, "text": text}, self_id, reply_author):
            skipped_group += 1
            continue
        replayed += 1

        now = float(ts)
        if uid == OWNER_ID:
            c0, p0 = state.BASE_C_OWNER, state.BASE_P_OWNER
        else:
            c0, p0 = state.BASE_C_OTHER, state.BASE_P_OTHER
        p = state.decay(person.get(uid) or state.fresh_person(now, c0, p0), now)
        w = state.weight(p, now)

        p = state.apply(p, state.BASELINE, now, w=w)
        fired = []
        for name, delta, strong in signals.events(text, now):
            mult = state.saturate(sat, f"{uid}|{name}", now)
            if mult < 0.34:
                continue
            p = state.apply(p, delta, now, strong, w=w * mult)
            fired.append(f"{name}x{mult:.2f}")
        person[uid] = p
        if fired:
            stats.append({"ts": datetime.fromtimestamp(now).strftime("%m-%d %H:%M"),
                          "uid": uid, "text": text.strip().replace("\n", " ")[:26],
                          "events": fired})

    payload = {"version": 3, "person": person,
               "sat": {k: v for k, v in sat.items() if v[0] > 0}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)

    now = datetime.now().timestamp()
    print(f"窗口 {start:%Y-%m-%d %H:%M} → {datetime.now():%Y-%m-%d %H:%M}")
    print(f"回放消息 {replayed} 条（跳过群里没叫她的 {skipped_group} 条），命中事件 {len(stats)} 条")
    print(f"写入 {OUT}")
    print("\n== 每个人的五轴 ==")
    for uid, p in sorted(person.items(), key=lambda kv: kv[1]["ts"]):
        v = state.values(state.decay(p, now), now)
        slow = state.slow_dev(state.decay(p, now))
        last = datetime.fromtimestamp(p["ts"]).strftime("%m-%d %H:%M")
        print(f"{uid:12s} {label.gauge_line(v)}  末次 {last}")
        print(f"{'':12s} 慢 {', '.join(f'{label.NAME[k]}{x:+.3f}' for k, x in slow.items())}")
    print("\n== 命中事件明细 ==")
    for s in stats:
        print(f"{s['ts']} {s['uid']:>10s} {','.join(s['events']):24s} {s['text']}")


if __name__ == "__main__":
    main()
