"""补充验证：
1) Bing HTML 返回的到底是什么（JS 壳 / 人机验证 / 真结果）
2) 360 / 夸克 / 头条 的真实链接结构（上一轮可能误过滤了跳转链接）
只读诊断。
"""

from __future__ import annotations

import asyncio
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MOB_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


async def main():
    q = "鸣潮 珂莱塔 养成攻略"
    e = quote_plus(q)
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as s:
        # ---- 1. Bing HTML 到底是什么 ----
        print("=" * 76)
        print("【1】Bing HTML 返回内容性质")
        for tag, ua, base in (("www-desktop", UA, "https://www.bing.com"),
                              ("m-mobile", MOB_UA, "https://m.bing.com")):
            async with s.get(f"{base}/search?q={e}&setlang=zh-CN&ensearch=0",
                             headers={"User-Agent": ua}) as r:
                txt = (await r.content.read(2 * 1024 * 1024)).decode(
                    r.charset or "utf-8", errors="replace")
            soup = BeautifulSoup(txt, "lxml")
            print(f"\n  --- {tag}  HTTP {r.status}  {len(txt)} 字节 ---")
            for kw in ("b_algo", "b_results", "captcha", "verify", "challenge",
                       "机器人", "验证", "enablejs", "noscript", "location.href"):
                if kw.lower() in txt.lower():
                    print(f"      含 {kw!r}")
            print(f"      script 标签 {len(soup.find_all('script'))}"
                  f"  noscript {len(soup.find_all('noscript'))}"
                  f"  a 总数 {len(soup.find_all('a'))}")
            # 页面里的中文可见文本
            body = soup.body.get_text(" ", strip=True) if soup.body else ""
            body = re.sub(r"\s+", " ", body)
            print(f"      可见文本前 220 字: {body[:220]}")
            await asyncio.sleep(0.8)

        # ---- 2. 国内源链接结构 ----
        print("\n" + "=" * 76)
        print("【2】国内源的真实链接结构（不过滤域名）")
        srcs = [
            ("so.com(360)", f"https://www.so.com/s?q={e}", UA),
            ("quark(夸克)", f"https://quark.sm.cn/s?q={e}", MOB_UA),
            ("toutiao(头条)", f"https://so.toutiao.com/search?keyword={e}", UA),
        ]
        for name, url, ua in srcs:
            try:
                async with s.get(url, headers={"User-Agent": ua}) as r:
                    txt = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
            except Exception as exc:
                print(f"\n  {name}: 异常 {str(exc)[:60]}")
                continue
            soup = BeautifulSoup(txt, "lxml")
            anchors = soup.find_all("a", href=True)
            hrefs = [a["href"] for a in anchors]
            # 常见跳转链接形式
            jump = [h for h in hrefs if re.search(r"(/link\?|/cklink|jump|url=|redirect)", h)]
            print(f"\n  --- {name}  HTTP {r.status}  {len(txt)}字节  a={len(anchors)}"
                  f"  跳转形链接={len(jump)} ---")
            # 内嵌 JSON？
            for marker in ("__INITIAL_STATE__", "__NEXT_DATA__", "window._SSR",
                           "INITIAL_STATE", "pageData", "searchData"):
                if marker in txt:
                    print(f"      内嵌数据标记: {marker}")
            for a in anchors:
                t = a.get_text(" ", strip=True)
                h = a["href"]
                if len(t) > 8 and not re.search(r"^(登录|网页|新闻|图片|视频|地图|更多|设置)",
                                                t):
                    print(f"        · {t[:46]:<48} {h[:56]}")
                    break
            for a in anchors[1:6]:
                t = a.get_text(" ", strip=True)
                if len(t) > 8:
                    print(f"          {t[:44]:<46} {a['href'][:54]}")
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
