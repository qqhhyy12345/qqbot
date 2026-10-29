"""诊断：QQ 空间图片为什么读不到。

步骤：
  1) 取 cookie
  2) 调说说列表接口，看每条说说有没有 pic 字段
  3) 对有图的说说，用三种请求头组合分别下载图片，对比结果：
       A. 只有 User-Agent（插件当前的做法）
       B. User-Agent + Referer
       C. User-Agent + Referer + Cookie
  4) 打印每种的 HTTP 状态与字节数，定位到底是哪一层拦住的

全程只读，凭证不落盘。
"""

import json
import ssl
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ONEBOT = "http://127.0.0.1:3000/get_cookies"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def get_cookies() -> tuple[str, str, str]:
    req = urllib.request.Request(
        ONEBOT,
        data=json.dumps({"domain": "user.qzone.qq.com"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        payload = json.loads(r.read().decode())
    s = (payload.get("data") or {}).get("cookies") or ""
    c = {}
    for item in s.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            c[k.strip()] = v.strip()
    return s, c.get("uin", ""), c.get("p_skey", "")


def hash33(skey: str) -> int:
    e = 5381
    for ch in skey:
        e += (e << 5) + ord(ch)
    return e & 0x7FFFFFFF


def list_posts(cookies: str, uin: str, gtk: int, num: int = 20) -> list[dict]:
    api = (
        "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/"
        "emotion_cgi_msglist_v6"
        f"?uin={uin}&ftype=0&sort=0&pos=0&num={num}&replynum=0"
        f"&g_tk={gtk}&code=2&format=json&need_private_comment=1"
    )
    req = urllib.request.Request(
        api,
        headers={
            "Cookie": cookies,
            "User-Agent": UA,
            "Referer": f"https://user.qzone.qq.com/{uin}",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", errors="replace").strip()
    if body.startswith("_Callback(") or body.startswith("callback("):
        body = body[body.index("(") + 1 : body.rindex(")")]
    return json.loads(body).get("msglist") or []


def try_download(url: str, headers: dict, label: str) -> str:
    try:
        req = urllib.request.Request(url, headers=headers)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            data = r.read()
            return f"{label}: HTTP {r.status}  {len(data):,} 字节"
    except urllib.error.HTTPError as e:
        return f"{label}: HTTP {e.code} 失败"
    except Exception as e:
        return f"{label}: 异常 {type(e).__name__} {str(e)[:60]}"


def main() -> int:
    cookies, uin, p_skey = get_cookies()
    if not (uin and p_skey):
        print("cookie 缺失，放弃")
        return 1
    gtk = hash33(p_skey)

    posts = list_posts(cookies, uin, gtk)
    print(f"拉到 {len(posts)} 条说说\n")

    total_pics = 0
    sampled = []
    for i, p in enumerate(posts, 1):
        pics = p.get("pic") or []
        videos = p.get("video") or []
        txt = (p.get("content") or "").replace("\n", " ").strip()[:28]
        print(f"[{i}] tid={p.get('tid')} 图片={len(pics)} 视频={len(videos)}  {txt or '(无正文)'}")
        if pics:
            total_pics += len(pics)
            keys = sorted({k for pic in pics for k in pic.keys()})
            print(f"     pic 字段名: {keys}")
            for j, pic in enumerate(pics):
                for key in ("url1", "url2", "url3", "smallurl", "bmiddle", "bigurl"):
                    if pic.get(key):
                        sampled.append((pic.get(key), key))
                        break
                if j >= 1:
                    break
        for v in videos:
            for key in ("url1", "pic_url"):
                if v.get(key):
                    print(f"     视频封面 {key}: {v[key][:90]}")
                    sampled.append((v[key], key))
                    break

    print(f"\n共 {total_pics} 张图片")
    if not sampled:
        print("\n本次抓到的说说里没有图片，无法验证下载链路。")
        print("提示：可以手动发一条带图的说说，或放宽 num 参数翻更早的。")
        return 0

    print("\n=== 下载对比 ===")
    for url, key in sampled[:3]:
        print(f"\n地址({key}): {url[:100]}")
        print("  " + try_download(url, {"User-Agent": UA}, "A 仅UA  "))
        print(
            "  "
            + try_download(
                url,
                {"User-Agent": UA, "Referer": f"https://user.qzone.qq.com/{uin}"},
                "B UA+Referer",
            )
        )
        print(
            "  "
            + try_download(
                url,
                {
                    "User-Agent": UA,
                    "Referer": f"https://user.qzone.qq.com/{uin}",
                    "Cookie": cookies,
                },
                "C UA+Ref+Cookie",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
