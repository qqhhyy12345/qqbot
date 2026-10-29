"""离线验证 PublishInvite：概率、冷却、安静时段、去重、真追加。

不碰网络、不碰 AstrBot 运行环境，用假 event / req 跑。
"""

import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.autonomy import INVITE_TEXT, PublishInvite

STATE = Path(r"D:\qqbot\astrbot\data\temp\autonomy_test_state.json")
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def fresh():
    STATE.unlink(missing_ok=True)
    return PublishInvite(STATE)


def fake_event(mid="msg-1"):
    return SimpleNamespace(message_obj=SimpleNamespace(message_id=mid))


def fake_req():
    return SimpleNamespace(
        extra_user_content_parts=[],
        system_prompt="原始系统提示（不许动）",
        contexts=[{"role": "user", "content": "你好"}],
    )


def main():
    import asyncio

    print("=" * 66)
    print("聊天触发式自主发说说 —— 离线验证")
    print("=" * 66)

    print("\n[1] 概率统计（10000 次独立掷骰）")
    random.seed(20261023)
    inv = fresh()
    inv._in_quiet_hours = lambda: False  # 排除时段影响
    hits = 0
    N = 10000
    for i in range(N):
        inv._state["last_invite_at"] = 0
        inv._state["publish_date"] = ""
        inv._state["invites_date"] = ""
        inv._state["invites_today"] = 0
        inv._seen.clear()
        if inv._should_invite(fake_event(f"m{i}")):
            hits += 1
    rate = hits / N
    check("命中率接近 3%", 0.025 <= rate <= 0.035, f"{rate:.3%}")

    print("\n[2] 真追加到请求（且不动 system_prompt）")
    random.seed(1)
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 1.0
    req = fake_req()
    ok = asyncio.run(inv.maybe_invite(fake_event("a"), req))
    texts = [getattr(p, "text", "") for p in req.extra_user_content_parts]
    check("命中并追加", ok and INVITE_TEXT in texts, f"{len(req.extra_user_content_parts)} 个块")
    check("system_prompt 未被改动", req.system_prompt == "原始系统提示（不许动）")
    check("contexts 未被改动", req.contexts == [{"role": "user", "content": "你好"}])

    print("\n[3] 冷却 2 小时")
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 1.0
    asyncio.run(inv.maybe_invite(fake_event("b1"), fake_req()))
    second = asyncio.run(inv.maybe_invite(fake_event("b2"), fake_req()))
    check("冷却期内不再邀请", not second)

    print("\n[4] 同一条消息只掷一次（工具循环不累积概率）")
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 0.0
    inv.COOLDOWN_SECONDS = 0
    n = 0
    for _ in range(5):
        if inv._should_invite(fake_event("same-id")):
            n += 1
    check("同一 message_id 5 次只掷 1 次", n == 0, "PROB=0 时不应命中")
    seen_after = len(inv._seen)
    check("已记录该消息", seen_after == 1, f"_seen={seen_after}")

    print("\n[5] 今日已发 → 不再邀请")
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 1.0
    inv.mark_published()
    check("发过就不邀请", not inv._should_invite(fake_event("c")))

    print("\n[6] 安静时段 2:00-8:00")
    inv = fresh()
    inv.PROB = 1.0
    inv._in_quiet_hours = lambda: True
    check("凌晨不邀请", not inv._should_invite(fake_event("d")))

    print("\n[7] 每日邀请上限 3 次")
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 1.0
    inv.COOLDOWN_SECONDS = 0
    cnt = 0
    for i in range(10):
        if inv._should_invite(fake_event(f"e{i}")):
            inv._state["last_invite_at"] = 0
            inv._bump_daily()
            cnt += 1
    check("最多 3 次", cnt == 3, f"实际 {cnt} 次")

    print("\n[8] 状态持久化（重启不丢）")
    inv = fresh()
    inv._in_quiet_hours = lambda: False
    inv.PROB = 1.0
    asyncio.run(inv.maybe_invite(fake_event("f"), fake_req()))
    inv2 = PublishInvite(STATE)
    check("重启后读到冷却状态", bool(inv2._state.get("last_invite_at")))

    print("\n[9] 异常不抛（req 缺字段时静默跳过）")
    inv = fresh()
    inv.PROB = 1.0
    inv._in_quiet_hours = lambda: False
    bad_req = SimpleNamespace()  # 没有 extra_user_content_parts
    try:
        r = asyncio.run(inv.maybe_invite(fake_event("g"), bad_req))
        check("坏 req 不抛错", r is False)
    except Exception as exc:
        check("坏 req 不抛错", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 66)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败: " + ", ".join(FAIL))
    print("=" * 66)

    STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
