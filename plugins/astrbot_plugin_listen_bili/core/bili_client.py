"""最小 B 站客户端 —— 只为拿音频流地址。不依赖 astrbot，便于独立测试。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from urllib.parse import urlencode

import aiohttp

BASE = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class MiniBili:
    def __init__(self, cookie: str = ""):
        self._wbi: tuple[str, str, float] | None = None
        self._cookie = (cookie or "").strip() or f"buvid3={__import__('uuid').uuid4()}"

    @staticmethod
    def _mixin(key: str) -> list[int]:
        return [ord(key[_TABLE[i]]) for i in range(32)]

    def _wbi_sign(self, params: dict) -> dict:
        if not self._wbi or time.time() - self._wbi[2] > 2 * 3600:
            raise RuntimeError("wbi 未初始化")
        img, sub, _ = self._wbi
        mixin = self._mixin(img + sub)
        params = {**params, "wts": int(time.time())}
        query = urlencode(dict(sorted(params.items())))
        params["w_rid"] = hashlib.md5((query + "".join(map(chr, mixin))).encode()).hexdigest()
        return params

    async def _get(self, session: aiohttp.ClientSession, url: str, params: dict,
                   *, signed: bool = False) -> dict:
        if signed:
            params = self._wbi_sign(params)
        headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": self._cookie}
        body = b""
        for attempt in (1, 2):  # 偶发 412/空响应，重试一次
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15),
                                   headers=headers) as resp:
                body = await resp.read()
            try:
                data = json.loads(body.decode("utf-8", "ignore"))
                break
            except json.JSONDecodeError:
                if attempt == 2:
                    raise RuntimeError(f"B站接口返回非JSON（疑似风控，body {len(body)}B）")
                await asyncio.sleep(2)
        if data.get("code") != 0:
            raise RuntimeError(f"B站接口 code={data.get('code')}: {data.get('message', '')[:80]}")
        return data

    async def init_wbi(self, session: aiohttp.ClientSession) -> None:
        # 先领真 buvid3/buvid4（裸随机 buvid 会被 412 风控页拦）
        async with session.get(f"{BASE}/x/frontend/finger/spi",
                               timeout=aiohttp.ClientTimeout(total=15),
                               headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}) as resp:
            data = await resp.json(content_type=None)
        d = data.get("data") or {}
        b3, b4 = d.get("b_3", ""), d.get("b_4", "")
        # 合并而不是覆盖：保留调用方传入的完整 cookie（SESSDATA 等）
        if b3:
            have = {kv.split("=", 1)[0] for kv in self._cookie.split(";") if "=" in kv}
            add = [kv for kv, name in ((f"buvid3={b3}", "buvid3"), (f"buvid4={b4}", "buvid4")) if name not in have]
            if add:
                self._cookie = (self._cookie + "; " + "; ".join(add)).strip("; ")

        # 未登录时 nav 返回 code=-101，但 body 里仍带 wbi_img（B站惯例），放行
        async with session.get(f"{BASE}/x/web-interface/nav", timeout=aiohttp.ClientTimeout(total=15),
                               headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": self._cookie}) as resp:
            data = await resp.json(content_type=None)
        wbi = (data.get("data") or {}).get("wbi_img") or {}
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img or not sub:
            raise RuntimeError("拿不到 wbi key")
        self._wbi = (img, sub, time.time())

    async def popular(self, session: aiohttp.ClientSession, ps: int = 20) -> list[dict]:
        data = await self._get(session, f"{BASE}/x/web-interface/popular", {"ps": ps})
        return (data.get("data") or {}).get("list") or []

    async def search(self, session: aiohttp.ClientSession, keyword: str, ps: int = 10) -> list[dict]:
        data = await self._get(session, f"{BASE}/x/web-interface/wbi/search/type",
                               {"search_type": "video", "keyword": keyword, "page": 1, "page_size": ps},
                               signed=True)
        return (data.get("data") or {}).get("result") or []

    async def get_audio_url(self, session: aiohttp.ClientSession, bvid: str) -> tuple[str, str, int]:
        """返回 (音频流地址, 标题, 时长秒)。"""
        detail = await self._get(session, f"{BASE}/x/web-interface/view", {"bvid": bvid})
        vdata = detail.get("data") or {}
        title, dur = vdata.get("title", ""), int(vdata.get("duration", 0))
        cid = ((vdata.get("pages") or [{}])[0]).get("cid")
        if not cid:
            raise RuntimeError("视频里没有分 P（cid 缺失）")
        play = await self._get(
            session, f"{BASE}/x/player/wbi/playurl",
            {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "qn": 16},
            signed=True,
        )
        pdata = play.get("data") or {}
        dash = pdata.get("dash") or {}
        audios = dash.get("audio") or []
        if audios:
            best = max(audios, key=lambda a: int(a.get("bandwidth", 0)))
            return best["baseUrl"], title, dur
        # 未登录态常给 mp4 durl（音视频合一），取它照样能解码出音轨
        durls = pdata.get("durl") or []
        if durls:
            return durls[0]["url"], title, dur
        raise RuntimeError("这个视频拿不到音频轨（可能是直播回放或特殊格式）")
