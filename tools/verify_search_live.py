"""线上验证（第二轮）：用她实际访问过的 URL，验证正文提取与 SSL 兜底。

只读调用，不改任何配置。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")

PLUGIN = r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_fetch_url\main.py"

# 她 19:22-19:23 实际访问过的链接 + 同类页面
URLS = [
    ("她抓到的垃圾页", "https://www.tianqi.com/nanjing/today/"),
    ("她 SSL 失败的页", "https://www.nmc.cn/publish/forecast/AJS/nanjing.html"),
    ("中国天气网南京", "https://www.weather.com.cn/weather/101020100.shtml"),
    ("正常文章页", "https://blog.csdn.net/blogdevteam"),
]


async def main():
    spec = importlib.util.spec_from_file_location("fetch_url_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.FetchUrlPlugin.__new__(mod.FetchUrlPlugin)

    for label, u in URLS:
        print("\n" + "=" * 72)
        print(f"【{label}】{u}")
        out = await plugin.fetch_url(None, u, 600)
        lines = [ln for ln in out.split("\n") if ln.strip()]
        print(f"  返回 {len(out)} 字 / {len(lines)} 行")
        for ln in lines[:16]:
            print("   ", ln[:110])


if __name__ == "__main__":
    asyncio.run(main())
