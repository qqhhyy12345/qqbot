"""诊断：deepseek-v4.1-flash 到底支不支持看图。

直接用 cmd_config.json 里的 provider 配置打一次真实请求，三种载荷对比：
  A. 纯文本（基线，确认密钥/接口正常）
  B. 文本 + 真实空间图片 URL
  C. 文本 + 1x1 像素 data URI（排除"只是这个图床被拦"的可能）

只打印 HTTP 状态和响应里的错误信息，绝不打印密钥。
"""

import base64
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CFG = r"D:\qqbot\astrbot\data\cmd_config.json"

# 1x1 红色 PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()

QZONE_PIC = (
    "http://b354.photo.store.qq.com/psb?/V14Itgff2H9nRX/"
    "M37Ghfo*3ILElqfNhGSns2MOf4FmOtZ7tXaEC.dPKC4!/m/dG"
)


def load_provider():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    pid = d["provider"][0]["id"]
    src_id = d["provider"][0]["provider_source_id"]
    src = next(s for s in d["provider_sources"] if s["id"] == src_id)
    key = src["key"]
    if isinstance(key, list):
        key = key[0] if key else ""
    return pid, d["provider"][0]["model"], src["api_base"].rstrip("/"), key


def call(api_base, key, model, content_parts, label):
    url = f"{api_base}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content_parts}],
        "max_tokens": 32,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        msg = data["choices"][0]["message"].get("content") or ""
        print(f"  {label}: HTTP 200  OK  回复={msg.strip()[:40]!r}")
        return True
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(raw)
            detail = (
                err.get("error", {}).get("message")
                or err.get("message")
                or json.dumps(err, ensure_ascii=False)
            )
        except Exception:
            detail = raw
        print(f"  {label}: HTTP {e.code}  失败")
        print(f"      错误：{str(detail)[:220]}")
        return False
    except Exception as e:
        print(f"  {label}: 异常 {type(e).__name__}: {str(e)[:120]}")
        return False


def main() -> int:
    pid, model, api_base, key = load_provider()
    print(f"provider={pid}  model={model}")
    print(f"api_base={api_base}")
    print(f"key: 已读取（长度 {len(key)}，不显示）\n")

    print("[A] 纯文本（基线）")
    call(api_base, key, model, [{"type": "text", "text": "说一个字：好"}], "A 纯文本")

    print("\n[B] 文本 + 真实空间图片 URL")
    call(
        api_base,
        key,
        model,
        [
            {"type": "text", "text": "这张图里是什么？一句话。"},
            {"type": "image_url", "image_url": {"url": QZONE_PIC}},
        ],
        "B 空间图",
    )

    print("\n[C] 文本 + 1x1 data URI")
    call(
        api_base,
        key,
        model,
        [
            {"type": "text", "text": "这张图里是什么？一句话。"},
            {"type": "image_url", "image_url": {"url": DATA_URI}},
        ],
        "C data URI",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
