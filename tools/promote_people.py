#!/usr/bin/env python3
"""把私聊里的记忆脱敏提升进 people 作用域。

people 是「对外档案」：让两套说话方式都认识他、都不用他重复解释，
但只留结论、不留私生活。默认 dry-run，--apply 才写。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_beyond"
MEMORY_ROOT = Path(r"D:\qqbot\bot-memory")
TOOL_DIR = Path(r"D:\qqbot\tools")
REPORT_DIR = TOOL_DIR / "reports"

sys.path.insert(0, PLUGIN_DIR)
sys.path.insert(0, str(TOOL_DIR))
from core import memstore  # noqa: E402
from compact_memory import call_llm  # noqa: E402

PLATFORM = "snowluma"

# 红档：这些词出现在对外档案里，说明脱敏没做到位
RED_FLAGS = [
    "林晚", "哈鲸鲸", "主人", "海子", "川西", "照片", "被骂", "骂了", "吵架",
    "分手", "前任", "抑郁", "生病", "医院", "工资", "贷款", "爸妈", "父母",
    "女朋友", "男朋友", "老婆", "老公",
]

BOUNDARY = "他的完整经历和私聊内容不在这一层，他本人在别处提起才接，不主动翻。"
MIN_LEN, MAX_LEN = 200, 3000

PROMPT = """你在整理一份「对外可见的人事档案」。下面是关于某个人的私聊记忆（现状档案 + 私聊原文），请写出脱敏后的新档案。

目标：让另一套说话方式完全不同的助手也能正确地对待他、不必让他重复解释，但不能暴露私生活细节。

保留（写成结论，不写过程）：
- 称呼、职业与专业、技能与动手能力、语言习惯、喜欢的沟通方式
- 他明确表达过的相处要求：哪些话别对他说、什么时候该认真、什么时候可以开玩笑
- 换个场合也成立、且由他本人说过的长期偏好

抽象：
- 家乡或所在地 → 只写到省
- 正在忙的事 → 只写「有压力的事在推进」，不写项目名、单位、人名

删除（绝不写入）：
- 具体事件经过、情绪细节、照片内容、家人、经济、健康、情感经历、精确地点、真名
- 你和他之间的互动过程（谁拒绝了谁、谁生气了、他先提了什么被否决）

格式：标准 frontmatter（name 与文件名一致，metadata.type 为 user），正文分三段：
【他是什么人】
【怎么跟他说话】
【别碰的】
正文最后固定一行：{boundary}

description 写一句判断「何时该读全文」的钩子，40 字以内。

只输出一个 JSON，包在 <people></people> 里：{{"description": "...", "content": "..."}}
content 不含 frontmatter。

======= 现状档案 =======
{current}

======= 私聊原文 =======
{sources}
"""


def sessions_for(store: memstore.MemoryStore, qq: str) -> list[tuple[str, memstore.ScopeStore]]:
    out = []
    for key in store.session_keys():
        if qq in key:
            scope = store.session_scope_by_key(key)
            if scope is not None:
                out.append((key, scope))
    # 私聊优先，群聊也带上（群里的 feedback 同样值得提升）
    out.sort(key=lambda kv: 0 if "FriendMessage" in kv[0] else 1)
    return out


async def collect_sources(store: memstore.MemoryStore, qq: str) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    for key, scope in sessions_for(store, qq):
        for name in scope.list_files():
            if name.startswith(f"user-{qq}") or name.startswith("feedback-"):
                content = await scope.read(name)
                if content:
                    docs.append((f"{key}/{name}", content))
    return docs


def extract_json(raw: str) -> dict | None:
    match = re.search(r"<people>(.*?)</people>", raw, re.S | re.I)
    body = match.group(1) if match else raw
    match = re.search(r"\{.*\}", body, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def check(text: str) -> list[str]:
    problems = []
    # 【别碰的】那一段本来就是在列举禁止项，词表扫到这里会全是误报
    scan = text
    idx = text.find("【别碰的】")
    if idx != -1:
        scan = text[:idx]
    for word in RED_FLAGS:
        if word in scan:
            problems.append(f"命中红档词「{word}」")
    if BOUNDARY not in text:
        problems.append("缺少固定边界句")
    if not ("【他是什么人】" in text and "【怎么跟他说话】" in text):
        problems.append("缺少分段标题")
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        problems.append(f"长度 {len(text)} 不在 {MIN_LEN}-{MAX_LEN} 之间")
    return problems


async def promote_one(store, platform: str, filename: str, apply: bool) -> dict:
    qq = filename[len("user-"):-len(".md")]
    result = {"file": f"{platform}/{filename}", "qq": qq, "ok": False, "problems": [], "notes": []}

    sources = await collect_sources(store, qq)
    if not sources:
        result["notes"].append("没有可用的私聊来源，跳过")
        return result

    people = store.people_scope(platform)
    current = await people.read(filename) or "（还没有）"

    prompt = PROMPT.format(
        boundary=BOUNDARY,
        current=current,
        sources="\n\n".join(f"--- {src} ---\n{txt}" for src, txt in sources),
    )
    payload = extract_json(call_llm(prompt))
    if not payload or not payload.get("content"):
        result["notes"].append("模型输出无法解析，本轮放弃")
        return result

    content = payload["content"].strip()
    description = (payload.get("description") or "").strip()
    problems = check(content)
    if not description:
        problems.append("description 为空")
    if len(description) > 60:
        problems.append("description 过长")

    result["problems"] = problems
    result["new_description"] = description
    result["new_content"] = content
    if problems:
        result["notes"].append("校验未通过，未写入")
        return result

    result["ok"] = True
    if apply:
        full = memstore.compose_memory(
            name=filename[:-len(".md")],
            description=description,
            mem_type="user",
            body=content,
            source=qq,
        )
        rep = await people.write(filename, full, source=qq)
        result["ok"] = rep.ok
        result["notes"].append(rep.message)
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description="私聊记忆脱敏提升进 people")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只出方案）")
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    store = memstore.MemoryStore(MEMORY_ROOT / "memories")
    people = store.people_scope(PLATFORM)
    if not people.root.is_dir():
        print("people 作用域不存在")
        return

    files = [f for f in people.list_files() if memstore.is_person_file(f)]
    if not files:
        print("people 里没有任何 user-<ID>.md，退出")
        return

    print(f"{'执行' if args.apply else '预演'} | 待处理 {len(files)} 个人")
    results = []
    for filename in files:
        r = await promote_one(store, PLATFORM, filename, args.apply)
        results.append(r)
        status = "已写入" if (args.apply and r["ok"]) else ("可写入" if r["ok"] else "跳过")
        print(f"\n[{status}] {r['file']}")
        for n in r["notes"]:
            print("   ·", n)
        for p in r["problems"]:
            print("   ✗", p)
        if r.get("new_content") and not args.apply:
            print("\n   ---- 脱敏后的档案 ----")
            print(f"   description: {r.get('new_description', '')}")
            for line in r["new_content"].splitlines():
                print("   " + line)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"promote-{stamp}.json"
    path.write_text(
        json.dumps({"stamp": stamp, "apply": args.apply, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告：{path}")


if __name__ == "__main__":
    asyncio.run(main())
