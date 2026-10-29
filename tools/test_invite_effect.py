"""实测：那段邀请到底会不会让林晚真的行动起来。

用真实的火山方舟接口 + 林晚的真实人格卡，对比「有邀请」和「没邀请」。
只测她的文字反应与工具调用倾向，不真的发说说。
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


def load_provider():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    p = d["provider"][0]
    src = next(s for s in d["provider_sources"] if s["id"] == p["provider_source_id"])
    key = src["key"][0] if isinstance(src["key"], list) else src["key"]
    return src["api_base"].rstrip("/") + "/chat/completions", key, p["model"]


def linwan_prompt():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT system_prompt FROM personas WHERE persona_id='linwan'")
    sp = cur.fetchone()[0]
    con.close()
    return sp


# 她可用的工具（简化成 shema，看模型会不会调）
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
                    "get_image": {"type": "boolean", "description": "是否带上当前对话的图片"},
                },
                "required": [],
            },
        },
    }
]


def ask(api, key, model, system, user_msgs, with_tool=True):
    msgs = [{"role": "system", "content": system}]
    msgs += user_msgs
    body = {"model": model, "messages": msgs, "max_tokens": 800, "temperature": 0.8}
    if with_tool:
        body["tools"] = TOOLS
    req = urllib.request.Request(
        api,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    ch = d["choices"][0]
    m = ch["message"]
    calls = m.get("tool_calls") or []
    return {
        "content": (m.get("content") or "").strip(),
        "tool_calls": [(c["function"]["name"], c["function"].get("arguments", "")) for c in calls],
        "usage": d.get("usage"),
    }


def show(label, r):
    print(f"\n--- {label}")
    if r["tool_calls"]:
        for name, args in r["tool_calls"]:
            print(f"  >>> 调用了工具 {name}")
            print(f"      参数: {args[:200]}")
    print(f"  文字: {r['content'][:180]!r}")
    u = r["usage"] or {}
    print(f"  token: 输入 {u.get('prompt_tokens')} 输出 {u.get('completion_tokens')}")


def main():
    api, key, model = load_provider()
    system = linwan_prompt()
    print("=" * 68)
    print(f"模型: {model}   人格卡 {len(system)} 字")
    print(f"邀请文本 {len(INVITE_TEXT)} 字")
    print("=" * 68)

    # 场景一：平淡的日常闲聊
    plain = [
        {"role": "user", "content": "今天写代码写得有点累"},
        {"role": "assistant", "content": "那就歇会儿呗，代码又不会跑"},
        {"role": "user", "content": "刚泡了杯茶"},
    ]
    show("A 无邀请（平淡对话）", ask(api, key, model, system, plain))

    invited = plain + [{"role": "user", "content": "刚泡了杯茶\n\n" + INVITE_TEXT}]
    show("B 有邀请（平淡对话）", ask(api, key, model, system, invited))

    # 场景二：有具体内容，容易产生想说的
    rich = [
        {"role": "user", "content": "我今天路过鸡鸣寺，樱花全开了，挤得跟赶集似的"},
        {"role": "assistant", "content": "樱花季嘛，人都往那儿凑"},
        {"role": "user", "content": "不过真好看，拍了几张"},
    ]
    show("C 无邀请（有内容）", ask(api, key, model, system, rich))
    show("D 有邀请（有内容）", ask(api, key, model, system, rich + [{"role": "user", "content": "拍了几张\n\n" + INVITE_TEXT}]))


if __name__ == "__main__":
    main()
