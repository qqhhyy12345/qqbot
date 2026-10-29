"""B 站 Web API 客户端（只读）。

要点：
- 部分接口（搜索、UP 主动态）要求 WBI 签名：从 nav 接口拿 img_key/sub_key，
  按 B 站公开的 mixin 表重排后对查询参数做 md5。缓存 key，约每 2 小时刷新一次足够。
- cookie 从插件配置整体粘贴（浏览器 F12 复制），未配置时公开接口仍可用。
- 所有请求带普通浏览器 UA + Referer，随机延时，失败重试一次——
  这套姿势在 fetch_url/百科通道上已验证有效。
- v1 只有只读接口；写操作（点赞/评论/发动态）二期再议（不可逆对外动作）。
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from functools import reduce
from urllib.parse import urlencode

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BASE = "https://api.bilibili.com"

# WBI mixin 表（B 站公开算法）
MixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliClient:
    def __init__(self, cookie: str = ""):
        self.cookie = (cookie or "").strip()
        self._wbi: tuple[str, str, float] | None = None  # (img_key, sub_key, ts)

    # ------------------------------------------------------------ 基础

    def _headers(self) -> dict:
        h = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    async def _get(self, url: str, params: dict | None = None, *, signed: bool = False,
                   allow_codes: tuple = (0,)) -> dict:
        if signed:
            params = await self._wbi_sign(params or {})
        last_err = ""
        for attempt in range(2):
            try:
                # 无关接口间的礼貌延时，降低风控概率
                await asyncio.sleep(random.uniform(0.3, 0.8))
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15), headers=self._headers()
                ) as s:
                    async with s.get(url, params=params) as r:
                        data = await r.json(content_type=None)
                code = data.get("code", -1)
                if code in allow_codes:
                    return data
                last_err = f"code={code} {data.get('message', '')}"
                # -352/-412 风控：稍等重试一次
                if code in (-352, -412) and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                break
            except Exception as e:
                last_err = str(e)
                if attempt == 0:
                    await asyncio.sleep(1.0)
        raise RuntimeError(f"B站接口失败: {last_err}")

    # ------------------------------------------------------------ WBI 签名

    async def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi and (time.time() - self._wbi[2]) < 2 * 3600:
            return self._wbi[0], self._wbi[1]
        # 未登录时 nav 返回 -101，但 body 里仍带 wbi_img（B站惯例），故放行该码
        data = await self._get(f"{BASE}/x/web-interface/nav", allow_codes=(0, -101))
        wbi = data.get("data", {}).get("wbi_img", {})
        img = wbi.get("img_url", "")
        sub = wbi.get("sub_url", "")
        img_key = re.search(r"(\w+)\.png", img)
        sub_key = re.search(r"(\w+)\.png", sub)
        if not img_key or not sub_key:
            raise RuntimeError("拿不到 WBI key（nav 接口异常）")
        self._wbi = (img_key.group(1), sub_key.group(1), time.time())
        return self._wbi[0], self._wbi[1]

    async def _wbi_sign(self, params: dict) -> dict:
        img_key, sub_key = await self._get_wbi_keys()
        mixin = "".join(
            (img_key + sub_key)[i] for i in MixinKeyEncTab
        )[:32]
        params = {**params, "wts": int(time.time())}
        params = {
            k: "".join(filter(lambda c: c not in "!'()*", str(v)))
            for k, v in sorted(params.items())
        }
        query = urlencode(params)
        params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        return params

    # ------------------------------------------------------------ 业务（只读）

    async def popular(self, page: int = 1) -> list[dict]:
        """热门视频（无需登录）。"""
        data = await self._get(
            f"{BASE}/x/web-interface/popular", {"ps": 20, "pn": page}
        )
        out = []
        for v in data.get("data", {}).get("list", []):
            out.append(
                {
                    "bvid": v.get("bvid", ""),
                    "title": re.sub(r"<.*?>", "", v.get("title", "")),
                    "up": v.get("owner", {}).get("name", ""),
                    "mid": v.get("owner", {}).get("mid", ""),
                    "play": v.get("stat", {}).get("view", 0),
                    "danmaku": v.get("stat", {}).get("danmaku", 0),
                    "desc": (v.get("desc", "") or "")[:80],
                }
            )
        return out

    async def search(self, keyword: str, page: int = 1, search_type: str = "video") -> list[dict]:
        """搜索（WBI 签名；登录态可提高成功率）。search_type: video 或 user。"""
        data = await self._get(
            f"{BASE}/x/web-interface/wbi/search/type",
            {"search_type": "bili_user" if search_type == "user" else search_type, "keyword": keyword, "page": page, "page_size": 20},
            signed=True,
        )
        out = []
        for r in (data.get("data", {}) or {}).get("result", []) or []:
            if search_type == "user":
                out.append(
                    {
                        "mid": r.get("mid", ""),
                        "up": r.get("uname", ""),
                        "fans": r.get("fans", 0),
                        "sign": (r.get("usign", "") or "")[:60],
                        "videos": r.get("videos", 0),
                    }
                )
            else:
                out.append(
                    {
                        "bvid": r.get("bvid", ""),
                        "title": re.sub(r"<.*?>", "", str(r.get("title", ""))),
                        "up": r.get("author", ""),
                        "mid": r.get("mid", ""),
                        "play": r.get("play", 0),
                        "desc": (r.get("description", "") or "")[:80],
                    }
                )
        return out

    async def video_detail(self, bvid: str) -> dict:
        """视频详情（无需登录）。"""
        data = await self._get(f"{BASE}/x/web-interface/view", {"bvid": bvid})
        v = data.get("data", {})
        return {
            "bvid": v.get("bvid", ""),
            "aid": v.get("aid", ""),
            "title": re.sub(r"<.*?>", "", v.get("title", "")),
            "up": v.get("owner", {}).get("name", ""),
            "mid": v.get("owner", {}).get("mid", ""),
            "desc": (v.get("desc", "") or "")[:300],
            "play": v.get("stat", {}).get("view", 0),
            "like": v.get("stat", {}).get("like", 0),
            "reply": v.get("stat", {}).get("reply", 0),
            "duration": v.get("duration", 0),
        }

    async def video_comments(self, bvid: str, count: int = 6) -> list[dict]:
        """热评（无需登录；type=1 为视频）。"""
        v = await self.video_detail(bvid)
        aid = v.get("aid") or ""
        data = await self._get(
            f"{BASE}/x/v2/reply",
            {"type": 1, "oid": aid, "ps": min(max(count, 1), 20), "sort": 1},
        )
        out = []
        for r in (data.get("data", {}) or {}).get("replies", []) or []:
            out.append(
                {
                    "user": r.get("member", {}).get("uname", ""),
                    "like": r.get("like", 0),
                    "text": (r.get("content", {}).get("message", "") or "")[:150],
                }
            )
        return out

    async def up_dynamics(self, host_mid: str, count: int = 8) -> list[dict]:
        """指定 UP 的图文/视频动态（需要登录 cookie + WBI）。"""
        if not self.cookie:
            raise RuntimeError("未配置 B 站登录 cookie，看不了 UP 主动态")
        data = await self._get(
            f"{BASE}/x/polymer/web-dynamic/v1/feed/space",
            {"host_mid": str(host_mid), "offset": ""},
            signed=True,
        )
        out = []
        for item in (data.get("data", {}) or {}).get("items", []) or []:
            if len(out) >= max(min(count, 15), 1):
                break
            modules = item.get("modules", {})
            author = (modules.get("module_author") or {}).get("name", "")
            ts = (modules.get("module_author") or {}).get("pub_ts", 0)
            mdynamic = modules.get("module_dynamic") or {}
            mdesc = (mdynamic.get("desc") or {}).get("text", "")
            major = mdynamic.get("major") or {}
            kind = major.get("type", "")
            extra = ""
            if kind.startswith("MAJOR_TYPE_ARCHIVE"):
                arc = major.get("archive", {})
                extra = f"[视频] {re.sub(r'<.*?>', '', arc.get('title', ''))}"
            elif kind.startswith("MAJOR_TYPE_DRAW"):
                draw = major.get("draw", {}) or {}
                n = len(draw.get("items", []) or [])
                extra = f"[图文{n}张]" if n else "[图文]"
            elif kind.startswith("MAJOR_TYPE_FORWARD"):
                fw = (major.get("forward") or {}).get("desc", {}).get("text", "") if isinstance(major.get("forward"), dict) else ""
                extra = f"[转发] {fw[:60]}"
            text = (mdesc or extra or item.get("id_str", "")).strip()
            if not text:
                continue
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                ts = 0
            out.append({"up": author, "time": ts, "text": text[:180]})
        return out
