"""验证 local_vision 的「来图时同步清扫」逻辑（不启动 AstrBot）。

策略：`_swap()` 在本轮有图时同步调 `_sweep_temp()`，删 temp 里
`media_image*` 且 mtime 超过 SWEEP_MIN_AGE 的文件，带 30 秒节流。

关键点：整个过程不排后台任务，所以 `_swap()` 返回时删除已经发生。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(r"D:\qqbot")
TEMP = ROOT / "astrbot" / "data" / "temp"

sys.path.insert(0, str(ROOT / "_uv_tools" / "astrbot" / "Lib" / "site-packages"))
sys.path.insert(0, str(ROOT / "astrbot" / "data" / "plugins"))

from astrbot_plugin_local_vision.main import LocalVisionPlugin as Plugin  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}{(' — ' + extra) if extra else ''}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}{(' — ' + extra) if extra else ''}")


def make_plugin() -> object:
    """绕开 __init__（它要 Context 和真实模型路径），只装清扫需要的字段。"""
    p = object.__new__(Plugin)
    p._last_sweep_at = 0.0
    p.DELETE_LOCAL_COPY = True
    p.SWEEP_MIN_AGE = 120.0
    p.SWEEP_MIN_INTERVAL = 30.0
    return p


def touch(path: Path, age_seconds: float, payload: bytes = b"x") -> Path:
    path.write_bytes(payload)
    ts = time.time() - age_seconds
    os.utime(path, (ts, ts))
    return path


async def fake_describe(src: str) -> str | None:
    return "一张测试图"


async def main() -> None:
    TEMP.mkdir(parents=True, exist_ok=True)
    print(f"temp = {TEMP}")
    print(
        f"SWEEP_MIN_AGE={Plugin.SWEEP_MIN_AGE}  "
        f"SWEEP_MIN_INTERVAL={Plugin.SWEEP_MIN_INTERVAL}\n"
    )

    # --- 1. 过期 vs 新鲜 ---
    p = make_plugin()
    old_img = touch(TEMP / "media_image_TEST_A_old.jpg", 300)
    fresh_img = touch(TEMP / "media_image_TEST_A_fresh.jpg", 5)
    n = await p._sweep_temp()
    check("过期 media_image 被删", not old_img.exists(), f"removed={n}")
    check("新鲜 media_image 不动", fresh_img.exists(), fresh_img.name)
    fresh_img.unlink(missing_ok=True)

    # --- 2. 前缀 / 目录边界 ---
    p = make_plugin()
    other_prefix = touch(TEMP / "edge_tts_TEST_B.wav", 300)
    outside = touch(ROOT / "media_image_TEST_B_outside.jpg", 300)
    await p._sweep_temp()
    check("非 media_image 前缀不删", other_prefix.exists(), other_prefix.name)
    check("temp 之外的文件不删", outside.exists(), outside.name)
    other_prefix.unlink(missing_ok=True)
    outside.unlink(missing_ok=True)

    # --- 3. 子目录里的同名文件不删（glob 只扫本层）---
    p = make_plugin()
    sub = TEMP / "_sweep_test_dir"
    sub.mkdir(exist_ok=True)
    sub_file = touch(sub / "media_image_TEST_C.jpg", 300)
    await p._sweep_temp()
    check("子目录里的不删", sub_file.exists(), str(sub_file))
    sub_file.unlink(missing_ok=True)
    sub.rmdir()

    # --- 4. 开关关闭：一个都不删 ---
    p = make_plugin()
    p.DELETE_LOCAL_COPY = False
    kept = touch(TEMP / "media_image_TEST_D.jpg", 300)
    n = await p._sweep_temp()
    check("DELETE_LOCAL_COPY=False 时不删", kept.exists() and n == 0, f"removed={n}")
    kept.unlink(missing_ok=True)

    # --- 5. 节流：30 秒内第二次调用直接返回 0 ---
    p = make_plugin()
    first = touch(TEMP / "media_image_TEST_E1.jpg", 300)
    n1 = await p._sweep_temp()
    check("首次调用执行清扫", not first.exists() and n1 == 1, f"removed={n1}")
    second = touch(TEMP / "media_image_TEST_E2.jpg", 300)
    n2 = await p._sweep_temp()
    check("30 秒内节流返回 0", second.exists() and n2 == 0, f"removed={n2}")
    n3 = await p._sweep_temp(force=True)
    check("force=True 跳过节流", not second.exists() and n3 == 1, f"removed={n3}")

    # --- 6. 节流窗口过后恢复 ---
    p = make_plugin()
    third = touch(TEMP / "media_image_TEST_F.jpg", 300)
    p._last_sweep_at = time.time() - 31  # 假装上次扫描是 31 秒前
    n = await p._sweep_temp()
    check("节流窗口过后会清扫", not third.exists() and n == 1, f"removed={n}")

    # --- 7. temp 不存在时不炸 ---
    p = make_plugin()
    p._temp_root = lambda: ROOT / "_no_such_temp_dir_xyz"  # type: ignore[method-assign]
    n = await p._sweep_temp()
    check("temp 不存在返回 0 不抛异常", n == 0, f"removed={n}")

    # --- 8. _swap 里同步触发：返回时旧图必然已删 ---
    p = make_plugin()
    p._cache = {}
    p._lock = asyncio.Lock()
    p._describe = fake_describe  # type: ignore[method-assign]
    old_for_swap = touch(TEMP / "media_image_TEST_G_old.jpg", 300)
    new_for_swap = touch(TEMP / "media_image_TEST_G_new.jpg", 0)
    req = types.SimpleNamespace(
        contexts=[],
        image_urls=[str(new_for_swap)],
        extra_user_content_parts=[],
    )
    await p._swap(req)
    # 注意：这里**没有任何 sleep**，返回即断言
    check("_swap 返回时旧图已删（无需等待）", not old_for_swap.exists(), old_for_swap.name)
    check("_swap 不误删本轮新图", new_for_swap.exists(), new_for_swap.name)
    check("本轮图已从 image_urls 移除", req.image_urls == [])
    check("已注入文字描述", len(req.extra_user_content_parts) == 1)
    new_for_swap.unlink(missing_ok=True)

    # --- 9. 本轮没图时不该清扫（策略即「来图才扫」）---
    p = make_plugin()
    p._cache = {}
    p._lock = asyncio.Lock()
    p._describe = fake_describe  # type: ignore[method-assign]
    untouched = touch(TEMP / "media_image_TEST_H.jpg", 300)
    req2 = types.SimpleNamespace(contexts=[], image_urls=[], extra_user_content_parts=[])
    await p._swap(req2)
    check("没图时不清扫（策略即「来图才扫」）", untouched.exists(), untouched.name)
    untouched.unlink(missing_ok=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)


asyncio.run(main())
