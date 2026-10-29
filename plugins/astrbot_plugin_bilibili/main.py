"""B 站浏览（只读版）—— 让她能逛 B 站：搜视频、看热门、看视频详情和热评、看 UP 主动态。

## v1 边界（有意为之）

- **只读**。点赞/评论/发动态是「不可逆的对外动作」，二期再议——
  账号是真人小号，写操作被风控的代价是整个账号。
- 工具保持 3 个（工具定义占上下文，能用一个参数解决的不开新工具）。

## 账号 cookie

在 WebUI 插件配置里整段粘贴浏览器 Cookie（F12 → Network → 任意请求 →
Request Headers → Cookie）。只影响登录态接口（UP 主动态）；未配置时
热门/搜索/视频详情照样能用。
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .bili import BiliClient

PLUGIN_NAME = "astrbot_plugin_bilibili"

# 输出体量控制（单工具结果尽量 < 800 token）
MAX_LIST = 8


def _fmt_videos(videos: list[dict]) -> str:
    lines = []
    for v in videos[:MAX_LIST]:
        play = v.get("play", 0)
        play_s = f"{play/10000:.1f}万" if play >= 10000 else str(play)
        lines.append(
            f"- {v.get('bvid','')}｜{v.get('title','')[:40]}｜UP:{v.get('up','')}｜{play_s}播放"
        )
    return "\n".join(lines) or "（没有结果）"


class BilibiliPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self.client = BiliClient(str(config.get("cookie", "")))
        self._last_call = 0.0

    async def _throttle(self):
        """最小间隔 1 秒，浏览器式的慢速浏览。"""
        wait = 1.0 - (time.time() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.time()

    @filter.llm_tool()
    async def bili_search(self, event: AiocqhttpMessageEvent, keyword: str, search_type: str = "video"):
        """在B站搜索。默认搜视频；找UP主本人时把 search_type 传 "user"（返回 mid/粉丝数/签名）。

        想找某类视频用默认；想找某个UP（尤其是不怎么发视频的画师类账号）、
        或想拿某UP的 mid 去看动态时，用 search_type="user"。
        拿到视频 bvid 后可用 bili_video 看详情和热评。

        Args:
            keyword(string): 搜索关键词
            search_type(string): "video"(默认) 搜视频；"user" 搜UP主
        """
        await self._throttle()
        st = "user" if str(search_type).lower() == "user" else "video"
        try:
            results = await self.client.search(keyword, search_type=st)
            if not results:
                return f"B站没搜到「{keyword}」的相关{'用户' if st == 'user' else '视频'}。"
            if st == "user":
                lines = [
                    f"- mid:{u['mid']}｜{u['up']}｜粉丝{u['fans']}"
                    + (f"｜{u['sign']}" if u.get("sign") else "")
                    for u in results[:MAX_LIST]
                ]
                return f"B站搜索「{keyword}」的UP主：\n" + ("\n".join(lines) or "（没有结果）")
            return f"B站搜索「{keyword}」：\n" + _fmt_videos(results)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] search 失败: {e}")
            return f"B站搜索失败：{e}"

    @filter.llm_tool()
    async def bili_hot(self, event: AiocqhttpMessageEvent):
        """看B站当前的热门视频榜单（≈10条，含 bvid/标题/UP主/播放量）。

        想「随便逛逛」「看看最近什么火」时用；对某条感兴趣就拿 bvid 调 bili_video。

        Args:
            (无参数)
        """
        await self._throttle()
        try:
            results = await self.client.popular()
            if not results:
                return "B站热门榜单暂时拿不到。"
            return "B站热门：\n" + _fmt_videos(results)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] hot 失败: {e}")
            return f"获取热门失败：{e}"

    @filter.llm_tool()
    async def bili_video(self, event: AiocqhttpMessageEvent, bvid: str, with_comments: bool = True):
        """看B站某个视频的详情：标题、简介、数据，以及热门评论。

        Args:
            bvid(string): 视频的 bvid 号（BV 开头，从 bili_search/bili_hot 的结果里拿）
            with_comments(boolean): 是否附带热门评论，默认 true
        """
        await self._throttle()
        try:
            v = await self.client.video_detail(bvid)
            if not v.get("title"):
                return f"没找到视频 {bvid}。"
            dur = v.get("duration", 0)
            dur_s = f"{dur//60}分{dur%60}秒" if dur else ""
            lines = [
                f"{v.get('title','')}（{v.get('up','')}）",
                f"播放{v.get('play',0)}｜点赞{v.get('like',0)}｜评论{v.get('reply',0)}"
                + (f"｜时长{dur_s}" if dur_s else ""),
                f"简介：{v.get('desc','') or '（无）'}",
            ]
            if with_comments:
                comments = await self.client.video_comments(bvid)
                if comments:
                    lines.append("热门评论：")
                    for c in comments[:5]:
                        lines.append(f"- {c['user']}（{c['like']}赞）：{c['text']}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] video 失败: {e}")
            return f"获取视频详情失败：{e}"

    @filter.llm_tool()
    async def bili_up_dynamics(self, event: AiocqhttpMessageEvent, up_id: str):
        """看某个UP主的最新动态（图文/视频/转发，≈8条）。需要已配置登录 cookie。

        up_id 可以是数字 mid，也可以先用 bili_search 搜 UP 名字从结果里拿 mid。

        Args:
            up_id(string): UP主的 mid（数字）
        """
        await self._throttle()
        try:
            items = await self.client.up_dynamics(str(up_id))
            if not items:
                return f"UP {up_id} 暂时没有可显示的动态（或未配置登录 cookie）。"
            lines = []
            for it in items:
                t = time.strftime("%m-%d %H:%M", time.localtime(it["time"])) if it.get("time") else ""
                lines.append(f"- [{t}] {it['up']}：{it['text']}")
            return "UP 主动态：\n" + "\n".join(lines)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] dynamics 失败: {e}")
            return f"获取UP主动态失败：{e}"
