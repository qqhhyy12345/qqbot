"""诊断 v2：模型看图能力 + 空间图片 URL 能否被方舟服务端下载。

修正 v1 的错误：v1 里硬编码的图片 URL 是打印时截断的（只到 100 字符），
所以 404 是测试自身的 bug，不是真实结论。

本版每次实时从说说接口取完整图片地址，不硬编码。
"""

import base64
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CFG = r"D:\qqbot\astrbot\data\cmd_config.json"
ONEBOT = "http://127.0.0.1:3000/get_cookies"


def load_provider():
    d = json.load(open(CFG, encoding="utf-8-sig"))
    pid = d["provider"][0]["id"]
    src_id = d["provider"][0]["provider_source_id"]
    src = next(s for s in d["provider_sources"] if s["id"] == src_id)
    key = src["key"]
    if isinstance(key, list):
        key = key[0] if key else ""
    return pid, d["provider"][0]["model"], src["api_base"].rstrip("/"), key


def get_cookies():
    req = urllib.request.Request(
        ONEBOT,
        data=json.dumps({"domain": "user.qzone.qq.com"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        payload = json.loads(r.read().decode())
    raw = (payload.get("data") or {}).get("cookies") or ""
    c = {}
    for item in raw.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            c[k.strip()] = v.strip()
    return raw, c.get("uin", ""), c.get("p_skey", "")


def hash33(skey):
    e = 5381
    for ch in skey:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def collect_pic_urls():
    cookies, uin, p_skey = get_cookies()
    gtk = hash33(p_skey)
    api = (
        "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/"
        "emotion_cgi_msglist_v6"
        f"?uin={uin}&ftype=0&sort=0&pos=0&num=30&replynum=0"
        f"&g_tk={gtk}&code=2&format=json&need_private_comment=1"
    )
    req = urllib.request.Request(
        api,
        headers={"Cookie": cookies, "User-Agent": UA, "Referer": f"https://user.qzone.qq.com/{uin}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", errors="replace").strip()
    if body.startswith("_Callback("):
        body = body[body.index("(") + 1 : body.rindex(")")]
    msgs = json.loads(body).get("msglist") or []
    urls = []
    for m in msgs:
        for pic in m.get("pic") or []:
            for k in ("url2", "url3", "url1", "smallurl"):
                if pic.get(k):
                    urls.append(pic[k])
                    break
    return cookies, urls


def call(api_base, key, model, parts, label, max_tokens=200):
    url = f"{api_base}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        msg = data["choices"][0]["message"].get("content") or ""
        print(f"  {label}: HTTP 200  回复={msg.strip()[:120]!r}")
        return True
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(raw)
            detail = err.get("error", {}).get("message") or json.dumps(err, ensure_ascii=False)
        except Exception:
            detail = raw
        print(f"  {label}: HTTP {e.code} 失败")
        print(f"      错误：{str(detail)[:260]}")
        return False
    except Exception as e:
        print(f"  {label}: 异常 {type(e).__name__}: {str(e)[:120]}")
        return False


def main() -> int:
    pid, model, api_base, key = load_provider()
    print(f"provider={pid}  model={model}\n")

    cookies, urls = collect_pic_urls()
    print(f"从空间拿到 {len(urls)} 个图片地址")
    if urls:
        u = urls[0]
        print(f"  首图完整长度: {len(u)} 字符")
        print(f"  开头: {u[:70]}")
        print(f"  结尾: ...{u[-40:]}\n")
    else:
        print("  没有图片，只测 data URI\n")
        u = None

    if u:
        print("[B] 文本 + 完整空间图片 URL（方舟服务端自己去下载）")
        call(
            api_base, key, model,
            [
                {"type": "text", "text": "这张图里有什么？用一句中文说。"},
                {"type": "image_url", "image_url": {"url": u}},
            ],
            "B 空间图(完整URL)",
        )

        print("\n[B2] 同上，但把图片先转成 base64 data URI（绕开服务端下载）")
        try:
            rq = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(rq, timeout=25) as r:
                blob = r.read()
            mime = "image/jpeg"
            if blob[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            data_uri = f"data:{mime};base64," + base64.b64encode(blob).decode()
            print(f"  本地下载成功 {len(blob):,} 字节")
            call(
                api_base, key, model,
                [
                    {"type": "text", "text": "这张图里有什么？用一句中文说。"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
                "B2 base64",
            )
        except Exception as e:
            print(f"  本地下载失败：{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
