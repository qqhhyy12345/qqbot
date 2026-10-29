"""验证：她调工具发完说说之后，会不会忘了回人话。

AstrBot 的流程是「调工具 -> 拿结果 -> 继续生成」，所以要看第二轮。
如果第二轮她还是不理人，那这套机制就有体验问题。
"""

import json
import sqlite3
import sys
import urllib.request

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.autonomy import INVITE_TEXT

CFG = r"D:\qqbot\astrbot\data\cmd_config.json"
DB = r"D:\qqbot\astrbot\data\data_v4.db"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "llm_publish_feed",
            "description": "写一篇说说并发布到QQ空间",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要发布的说说内容"},
                    "get_image": {"type": "boolean"},
                },
                "required": [],
            },
        },
    }
]


def setup():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    p = d["provider"][0]
    src = next(s for s in d["provider_sources"] if s["id"] == p["provider_source_id"])
    key = src["key"][0] if isinstance(src["key"], list) else src["key"]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT system_prompt FROM personas WHERE persona_id='linwan'")
    sp = cur.fetchone()[0]
    con.close()
    return src["api_base"].rstrip("/") + "/chat/completions", key, p["model"], sp


def call(api, key, model, messages, tools=True):
    body = {"model": model, "messages": messages, "max_tokens": 800, "temperature": 0.8}
    if tools:
        body["tools"] = TOOLS
    req = urllib.request.Request(
        api,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]


def main():
    api, key, model, system = setup()
    conv = [
        {"role": "user", "content": "今天写代码写得有点累"},
        {"role": "assistant", "content": "那就歇会儿呗，代码又不会跑"},
        {"role": "user", "content": "刚泡了杯茶\n\n" + INVITE_TEXT},
    ]
    msgs = [{"role": "system", "content": system}] + conv

    print("=" * 68)
    print("第 1 轮：她拿到邀请")
    m1 = call(api, key, model, msgs)
    calls = m1.get("tool_calls") or []
    print(f"  文字: {(m1.get('content') or '(空)')[:100]!r}")
    print(f"  工具: {[c['function']['name'] for c in calls]}")
    if not calls:
        print("\n  这轮没调工具，换个场景再看")
        return
    arg = json.loads(calls[0]["function"].get("arguments") or "{}")
    print(f"  发的说说: {arg.get('text')!r}")

    print("\n" + "=" * 68)
    print("第 2 轮：把工具结果塞回去，看她会不会补上人话")
    msgs.append({"role": "assistant", "content": m1.get("content") or "", "tool_calls": calls})
    for c in calls:
        msgs.append({
            "role": "tool",
            "tool_call_id": c.get("id") or c["function"]["name"],
            "content": "已发布\n" + (arg.get("text") or ""),
        })
    m2 = call(api, key, model, msgs)
    txt = (m2.get("content") or "").strip()
    more = m2.get("tool_calls") or []
    print(f"  第二轮文字: {txt!r}")
    print(f"  又调工具: {[c['function']['name'] for c in more]}")

    ok = bool(txt) and "茶" in txt or "喝" in txt or len(txt) > 3
    print()
    print(f"  {'PASS' if ok else 'FAIL'} 发完说说仍然正常回话")
    if not ok:
        print("  注意：她为了发说说把人晾着了，需要调 prompt")


if __name__ == "__main__":
    main()
