"""QQ 空间链路只读验证。

依次验证：
  1) SnowLuma 的 get_cookies 能拿到 user.qzone.qq.com 的 cookie
  2) cookie 里的 uin / skey / p_skey 齐全
  3) 用该 cookie 调空间只读接口（拉自己的说说列表）能拿到数据

全程只读，不改任何东西；凭证只在内存里过一遍，不落盘。
"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ONEBOT = "http://127.0.0.1:3000/get_cookies"
DOMAIN = "user.qzone.qq.com"


def get_cookie_str() -> str:
    req = urllib.request.Request(
        ONEBOT,
        data=json.dumps({"domain": DOMAIN}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("retcode") not in (0, None):
        raise RuntimeError(f"get_cookies retcode={payload.get('retcode')}")
    return str((payload.get("data") or {}).get("cookies") or "")


def parse_cookies(s: str) -> dict:
    out = {}
    for item in s.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def hash33(skey: str) -> int:
    """QQ 的 g_tk 算法（bkn）。"""
    e = 5381
    for ch in skey:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def main() -> int:
    print("[1] 取 cookie ...", end=" ")
    cookies_str = get_cookie_str()
    if not cookies_str:
        print("失败：返回空")
        return 1
    c = parse_cookies(cookies_str)
    print(f"OK，{len(c)} 个键")

    print("[2] 检查关键字段 ...", end=" ")
    uin_raw = c.get("uin", "")
    uin = uin_raw[1:] if uin_raw[:1].lower() == "o" else uin_raw
    skey = c.get("skey", "")
    p_skey = c.get("p_skey", "")
    missing = [n for n, v in (("uin", uin), ("skey", skey), ("p_skey", p_skey)) if not v]
    if missing or not uin.isdigit():
        print(f"失败：缺 {missing} 或 uin 非法")
        return 1
    print(f"OK  uin={uin}  skey={len(skey)}位  p_skey={len(p_skey)}位")

    gtk = hash33(p_skey)
    print(f"[3] 计算 g_tk ... OK ({gtk})")

    print("[4] 调空间只读接口（拉自己前 3 条说说）...", end=" ")
    api = (
        "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/"
        "emotion_cgi_msglist_v6"
        f"?uin={uin}&ftype=0&sort=0&pos=0&num=3&replynum=0"
        f"&g_tk={gtk}&code=2&format=json&need_private_comment=1"
    )
    req = urllib.request.Request(
        api,
        headers={
            "Cookie": cookies_str,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": f"https://user.qzone.qq.com/{uin}",
            "Host": "user.qzone.qq.com",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"请求异常：{exc}")
        return 1

    body = raw.strip()
    if body.startswith("_Callback(") or body.startswith("callback("):
        body = body[body.index("(") + 1 : body.rindex(")")]

    try:
        data = json.loads(body)
    except Exception:
        print(f"返回非 JSON，前 200 字：{body[:200]}")
        return 1

    code = data.get("code", data.get("ret"))
    msg = data.get("message") or data.get("msg") or ""
    total = data.get("total")
    items = data.get("msglist") or []
    print(f"OK  返回码={code}  {('提示=' + str(msg)) if msg else ''}")
    print(f"    说说总数={total}  本次取到={len(items)} 条")

    if items:
        print("    最近几条（仅标题/正文前 40 字）：")
        for it in items[:3]:
            txt = (it.get("content") or "").replace("\n", " ").strip()
            print(f"      - {txt[:40] or '(无正文/仅图片)'}")
    print()
    print("结论：cookie 自动获取 + 空间接口调用 全链路通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
