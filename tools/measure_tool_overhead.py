"""实测「工具定义 + 系统提示」占上下文的比例。

工具定义的 JSON 会随 func_tool 每次请求全量发送，是固定开销里最大的一块。
用方舟 API 的 usage.prompt_tokens 精确测，不靠估算。
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import re
from pathlib import Path

import aiohttp

ROOT = Path(r"D:\qqbot")
PLUGINS = ROOT / "astrbot" / "data" / "plugins"

CFG = json.load(io.open(ROOT / "astrbot" / "data" / "cmd_config.json", encoding="utf-8-sig"))
SRC = next(p for p in CFG["provider_sources"] if "ark" in str(p.get("api_base", "")))
KEY = SRC["key"][0] if isinstance(SRC["key"], list) else SRC["key"]
BASE = str(SRC["api_base"]).rstrip("/")
MODEL = next(p for p in CFG["provider"] if "ark" in str(p.get("id", "")))["model"]

PY_TYPE = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "list": "array", "dict": "object",
}


def harvest() -> list[dict]:
    """从插件源码里抠出所有 llm_tool 的签名与 docstring，拼成 OpenAI tools 格式。"""
    tools: list[dict] = []
    for py in PLUGINS.rglob("main.py"):
        try:
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:  # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            deco = ast.unparse(node.decorator_list) if node.decorator_list else ""
            if "llm_tool" not in deco:
                continue
            doc = ast.get_docstring(node) or ""
            props: dict[str, dict] = {}
            required: list[str] = []
            # ast.arg 没有 default 属性；默认值在 node.args.defaults 里，
            # 且只对应**末尾**的若干个位置参数。
            args = [a for a in node.args.args if a.arg not in ("self", "event")]
            first_default = len(args) - len(node.args.defaults)
            for i, arg in enumerate(args):
                ann = ast.unparse(arg.annotation) if arg.annotation else "str"
                base = ann.replace("| None", "").strip()
                desc = ""
                for line in doc.splitlines():
                    if f"{arg.arg}(" in line:
                        desc = line.split("):", 1)[-1].strip()
                props[arg.arg] = {"type": PY_TYPE.get(base, "string"), "description": desc}
                if i < first_default:
                    required.append(arg.arg)
            tools.append({
                "type": "function",
                "function": {
                    "name": node.name,
                    "description": doc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
                "_plugin": py.parent.name,
            })
    return tools


async def count(session: aiohttp.ClientSession, payload: dict) -> int:
    body = {"model": MODEL, "messages": [{"role": "user", "content": "x"}], "max_tokens": 1}
    body.update(payload)
    async with session.post(
        f"{BASE}/chat/completions", json=body,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as r:
        return (await r.json()).get("usage", {}).get("prompt_tokens", 0)


async def main() -> None:
    tools = harvest()
    blob = json.dumps(tools, ensure_ascii=False)
    print(f"模型 {MODEL}\n抠到 {len(tools)} 个 LLM 工具\n")
    print("=" * 78)
    print(f"{'工具':26s} {'所属插件':26s} {'说明字符':>8s}")
    print("=" * 78)
    for t in sorted(tools, key=lambda x: -len(x["function"]["description"])):
        print(f"{t['function']['name']:26s} {t['_plugin']:26s} {len(t['function']['description']):8d}")
    print("=" * 78)
    print(f"{'合计':26s} {'':26s} {sum(len(t['function']['description']) for t in tools):8d}")

    async with aiohttp.ClientSession() as s:
        base = await count(s, {})
        with_tools = await count(s, {"tools": tools})
    delta = with_tools - base
    print(f"\n无工具基线      : {base:6d} token")
    print(f"带 11 个工具定义: {with_tools:6d} token")
    print(f"→ 工具定义开销  : {delta:6d} token\n")
    # 每工具平均
    print(f"平均每工具约 {delta/max(len(tools),1):.0f} token")
    for t in sorted(tools, key=lambda x: -len(x["function"]["description"]))[:5]:
        print(f"  {t['function']['name']:20s} 说明 {len(t['function']['description']):5d} 字符")


if __name__ == "__main__":
    asyncio.run(main())