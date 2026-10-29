import time

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

PLUGIN_NAME = "astrbot_plugin_contact_list"

# 缓存有效期：一天刷新一次（好友/群列表几乎只在过好友请求时变）
CACHE_TTL = 86400


class ContactListPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._cache_text: str | None = None
        self._cache_at: float = 0.0

    # ------------------------------------------------------------ 内部

    def _get_bot(self):
        platform = self.context.get_platform("aiocqhttp")
        if platform is None:
            raise RuntimeError("aiocqhttp 平台未就绪")
        bot = getattr(platform, "bot", None)
        if bot is None:
            raise RuntimeError("aiocqhttp client 未就绪")
        return bot

    async def _fetch_summary(self, self_id: str) -> str:
        bot = self._get_bot()
        friends = await bot.call_action("get_friend_list")
        groups = await bot.call_action("get_group_list")

        friend_parts = []
        for f in friends:
            uid = str(f.get("user_id", ""))
            if uid == self_id:
                continue  # 她自己
            remark = (f.get("remark") or "").strip()
            nick = (f.get("nickname") or "").strip()
            name = remark if remark and remark != nick else nick
            friend_parts.append(f"{uid} {name}" + (f"({nick})" if remark and remark != nick else ""))

        group_parts = []
        for g in groups:
            gid = g.get("group_id", "")
            gname = (g.get("group_name") or "").strip() or str(gid)
            cnt = g.get("member_count", "?")
            group_parts.append(f"{gid} {gname}({cnt}人)")

        lines = [f"好友({len(friend_parts)})：{'｜'.join(friend_parts) or '无'}"]
        if group_parts:
            lines.append(f"群({len(group_parts)})：{'｜'.join(group_parts)}")
        return "\n".join(lines)

    def _cached(self) -> str | None:
        if self._cache_text is not None and (time.time() - self._cache_at) < CACHE_TTL:
            return self._cache_text
        return None

    # ------------------------------------------------------------ LLM 工具

    @filter.llm_tool()
    async def llm_list_contacts(self, event: AiocqhttpMessageEvent, refresh: bool = False):
        """查看你的QQ好友列表和群聊列表（含每个好友的QQ号和备注）。仅私聊时可用。

        平时直接调用（返回的是缓存，每天自动更新一次，足够新鲜）；
        只有当主人明确说「我加了新好友 / 我加了新群 / 删了好友」时，
        才传 refresh=true 强制重新拉取。

        Args:
            refresh(boolean): 是否强制刷新缓存，默认 False（用缓存即可）
        """
        if event.get_group_id():
            return "好友/群列表只在私聊里提供，群聊里不查。"
        self_id = str(event.get_self_id())
        if not refresh:
            cached = self._cached()
            if cached is not None:
                return cached
        try:
            text = await self._fetch_summary(self_id)
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 拉取列表失败: {e}")
            return f"拉取好友/群列表失败：{e}"
        self._cache_text = text
        self._cache_at = time.time()
        return text
