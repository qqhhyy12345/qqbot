"""诊断 fetch_url 正文提取为什么抓回导航/无关内容。

看两件事：
1) 容器选择逻辑选中了谁，候选都是谁
2) 选中之后里面到底装了什么
只读诊断。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

import aiohttp
from bs4 import BeautifulSoup

PLUGIN = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py")
SRC = PLUGIN.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
LINES = SRC.splitlines()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
     "Accept-Encoding": "gzip, deflate"}

ns = {"re": re, "BeautifulSoup": BeautifulSoup}
for node in TREE.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                try:
                    e = ast.Expression(node.value)
                    ast.fix_missing_locations(e)
                    ns[t.id] = eval(compile(e, "<c>", "eval"), {"re": re}, {})
                except Exception:
                    pass
for node in TREE.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_extract":
        exec("\n".join(LINES[node.lineno - 1:node.end_lineno]), ns)
_extract = ns["_extract"]

URLS = [
    "https://www.tianqi.com/nanjing/today/",
    "https://www.weather.com.cn/weather/101020100.shtml",
]


async def main():
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, headers=H) as s:
        for url in URLS:
            try:
                async with s.get(url, headers=H, allow_redirects=True) as r:
                    html = (await r.content.read(2 * 1024 * 1024)).decode(
                        r.charset or "utf-8", errors="replace")
                    print("\n" + "=" * 74)
                    print(f"{url}  HTTP {r.status}  {len(html)} 字节")
            except Exception as exc:
                print(f"\n{url}  异常 {type(exc).__name__}: {str(exc)[:70]}")
                continue

            soup = BeautifulSoup(html, "lxml")
            # 复现 _extract 的候选选择
            cands = soup.find_all(["article", "main", "div", "section"])
            print(f"  候选容器 {len(cands)} 个")
            scored = []
            for c in cands:
                p_len = sum(len(p.get_text(strip=True)) for p in c.find_all("p"))
                own = len(c.get_text(strip=True))
                link_len = sum(len(a.get_text(strip=True)) for a in c.find_all("a"))
                scored.append((p_len, own, link_len, c))
            scored.sort(key=lambda x: -x[0])

            print(f"\n  {'p文字':>7}{'总文字':>8}{'链接文字':>9}{'链接占比':>8}  容器标识")
            for p_len, own, link_len, c in scored[:8]:
                ident = " ".join(filter(None, [
                    c.get("id") or "",
                    " ".join(c.get("class") or []) if isinstance(c.get("class"), list)
                    else (c.get("class") or "")]))[:42]
                ratio = link_len / own * 100 if own else 0
                print(f"  {p_len:>7}{own:>8}{link_len:>9}{ratio:>7.0f}%  {ident}")

            best = scored[0][3] if scored else None
            if best is not None:
                print(f"\n  ★ 当前逻辑选中：p文字={scored[0][0]} 总文字={scored[0][1]} "
                      f"链接文字={scored[0][2]}")
                # 最内层的高分容器
                inner = [x for x in scored if x[3] is not best and x[0] == scored[0][0]]
                print(f"  同样分数的容器 {len(inner)} 个 → 说明 find_all('p') 是递归的，"
                      f"父子分数相同")

            title, text = _extract(html)
            print(f"\n  _extract 输出 {len(text)} 字，前 500 字：")
            print("   ", text[:500].replace("\n", " | "))
            await asyncio.sleep(1.2)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
