# 一次性测量：deepseek-v4.1-flash 在不同 reasoning_effort 下的思考 token 真实占比
import json, io, statistics, urllib.request

cfg = json.load(io.open(r"D:/qqbot/astrbot/data/cmd_config.json", encoding="utf-8-sig"))
prov = next(p for p in cfg["provider"] if p.get("id") == "volcengine-ark")
src = next(s for s in cfg["provider_sources"] if s.get("id") == prov.get("provider_source_id")) if cfg.get("provider_sources") else None
key = (src or {}).get("key")
base = ((src or {}).get("api_base") or "").rstrip("/")
if isinstance(key, list):
    key = key[0]
model = prov.get("model") or "deepseek-v4.1-flash"

# 贴近真实场景：一个 ~1.5k 字符的 system（人格+工具说明的量级）+ 一句日常提问
system = (
    "你是测试人格。聊天风格：口语化、简短。"
    "你有这些工具可用：search_web（联网搜索）、fetch_url（抓网页）、memory_read/memory_write（记事本）、"
    "bili_search/bili_hot/bili_video（B站浏览）、llm_view_feed/llm_publish_feed（QQ空间）。"
    "使用规则：查资料先 search_web，看具体页面用 fetch_url；用户提到看过的东西不确定就问；"
    "记忆相关先 memory_read 再答；这是普通聊天，不必动用工具。"
) * 6  # 拉到接近真实 system 体量
question = "我今天看了一个视频，那个人打鼓好像很厉害，你说我该去学吗？"

def ask(effort):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": question}],
        "reasoning_effort": effort,
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=120).read())
    u = d["usage"]
    det = (u.get("completion_tokens_details") or {})
    return u.get("prompt_tokens", 0), u.get("completion_tokens", 0), det.get("reasoning_tokens", 0)

for eff in ("low", "high", "none"):
    res = [ask(eff) for _ in range(3)]
    pt = [r[0] for r in res]; ct = [r[1] for r in res]; rt = [r[2] for r in res]
    med_r, med_c = statistics.median(rt), statistics.median(ct)
    print(f"{eff:5s} reasoning={med_r:6.0f} (min {min(rt)}/max {max(rt)})  completion={med_c:6.0f}  "
          f"think占输出 {med_r/med_c*100:4.1f}%  prompt≈{statistics.median(pt):.0f}")
