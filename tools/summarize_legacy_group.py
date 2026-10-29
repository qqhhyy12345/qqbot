"""把 4 条旧分裂群会话各摘要一段，供合并进共享群会话。"""

import asyncio
import json
import sqlite3
from pathlib import Path

import aiohttp

DB = r"D:\qqbot\astrbot\data\data_v4.db"
CFG = r"D:\qqbot\astrbot\data\cmd_config.json"
OUT = Path(r"D:\qqbot\bot-memory\archive\_legacy_group_split")

LEGACY = [
    ("snowluma:GroupMessage:<OWNER_QQ>_144808575", "你（主人）本人"),
    ("snowluma:GroupMessage:3576388696_144808575", "群友 3576388696"),
    ("snowluma:GroupMessage:3825551887_144808575", "群友 3825551887"),
    ("snowluma:GroupMessage:2501413156_144808575", "群友 2501413156"),
]

PROMPT = (
    "你是对话归档员。下面是你（哈鲸鲸）和{who}在 QQ 群里的一段聊天记录，"
    "当时是按人分开存的。请压缩成简洁的记忆摘要。\n"
    "要求：\n"
    "1. 保留事实、约定、在做的事、称呼与人物关系、情绪基调；\n"
    "2. 丢掉寒暄、重复、语气词、系统提示；\n"
    "3. 按时间顺序，短句陈述，不要分点符号，不要标题；\n"
    "4. 控制在 300 字以内；\n"
    "5. 中文，只输出摘要正文，不要任何额外说明。\n\n聊天记录：\n"
)


def text_of(msg) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def transcript(raw: str) -> str:
    msgs = json.loads(raw)
    out = []
    for m in msgs:
        t = text_of(m).strip()
        if not t or t.startswith("<system"):
            continue
        tag = {"user": "对方", "assistant": "我"}.get(m.get("role"), "?")
        out.append(f"[{tag}] {t}")
    return "\n".join(out)


def load_cfg():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    src = d["provider_sources"][0]
    key = src["key"]
    if isinstance(key, list):
        key = key[0]
    return src["api_base"].rstrip("/"), key, d["provider"][0]["model"]


async def summarize(sess, base, key, model, prompt):
    url = f"{base}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8000,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with sess.post(url, json=payload, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=300)) as r:
        body = await r.text()
    if r.status != 200:
        return f"(失败 HTTP {r.status}: {body[:200]})"
    j = json.loads(body)
    ch = j["choices"][0]
    msg = ch.get("message") or {}
    txt = (msg.get("content") or "").strip()
    usage = j.get("usage") or {}
    print(f"    finish={ch.get('finish_reason')} "
          f"completion={usage.get('completion_tokens')} "
          f"reasoning={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens')}")
    if not txt:
        # 推理吃光额度时会返回空 content，退一步用最简提示重试
        payload["messages"] = [
            {"role": "user", "content": "直接输出结论，不要思考过程。\n" + prompt}
        ]
        async with sess.post(url, json=payload, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=300)) as r2:
            j2 = json.loads(await r2.text())
        ch2 = j2["choices"][0]
        txt = ((ch2.get("message") or {}).get("content") or "").strip()
        print(f"    重试 finish={ch2.get('finish_reason')} 长度={len(txt)}")
    return txt


async def main():
    base, key, model = load_cfg()
    print(f"模型 {model}  端点 {base}")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = {u: ct for u, ct in c.execute("select user_id, content from conversations")}

    results = {}
    async with aiohttp.ClientSession(trust_env=False) as sess:
        for umo, who in LEGACY:
            raw = rows.get(umo)
            if not raw:
                print(f"跳过（不存在）{umo}")
                continue
            txt = transcript(raw)
            print(f"\n=== {who}  文本 {len(txt)} 字符，摘要中… ===")
            s = await summarize(sess, base, key, model, PROMPT.format(who=who) + txt)
            print(s)
            results[umo] = {"who": who, "summary": s, "chars": len(s)}

    (OUT / "summaries.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = ["# 群 144808575 旧分裂会话摘要（合并前留档）\n"]
    for umo, r in results.items():
        md.append(f"## {r['who']}\n\n{r['summary']}\n")
    (OUT / "summaries.md").write_text("\n".join(md), encoding="utf-8")
    total = sum(r["chars"] for r in results.values())
    print(f"\n共 {len(results)} 段，合计 {total} 字，已写入 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
