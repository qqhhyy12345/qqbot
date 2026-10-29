"""聊天触发式自主发说说。

不设定时器、不额外调模型。只在她本来就要发请求的前一刻，按小概率
往消息尾部追加一句邀请；没命中就什么都不加 —— 零成本。

为什么追加到 extra_user_content_parts 而不是 system_prompt：
system_prompt 是缓存前缀，改它会让整段历史缓存失效（今天实测过，
改人格卡导致 96K token 全量重算）。extra_user_content_parts 在消息
尾部，前缀缓存仍然命中。

为什么不用 cron：
cron 是「到点必发」，那不是她决定，是机器决定。这里改成「聊天时偶尔
被提醒一次，发不发她自己定」——她想发就调工具发，不想就无视。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.core.agent.message import TextPart

INVITE_TEXT = (
    "（顺口一句：要是你正好想发条说说，现在直接调发说说工具发出去就行，"
    "不用问谁；想看看谁的说说、顺手评一句也行，看完自己挑，别勉强。"
    "没什么想做的就当我没提。这段不用回，也别提。）"
)


class PublishInvite:
    """偶尔在聊天里提醒她「想发就可以发」，其余什么都不做。"""

    # 命中概率：一天几十上百条消息，3% 大约换来 2~3 次邀请
    PROB = 0.03
    # 两次邀请至少隔这么久，避免连着推
    COOLDOWN_SECONDS = 2 * 3600
    # 凌晨不邀请：那时候发说说太假
    QUIET_START = 2
    QUIET_END = 8
    # 一天最多邀请几次（防止反复骚扰她）
    DAILY_INVITE_MAX = 3
    # 同一条消息在工具循环里会被多次触发，只掷一次骰子
    SEEN_MSG_KEEP = 60

    def __init__(self, state_path: str | Path, timezone=None):
        self.state_path = Path(state_path)
        self.timezone = timezone
        self._state = self._load()
        self._seen: list[str] = []

    # ---------- 对外 ----------

    async def maybe_invite(self, event, req) -> bool:
        """命中就追加邀请，返回是否追加了。永不抛错。"""
        try:
            if not self._should_invite(event):
                return False

            req.extra_user_content_parts.append(TextPart(text=INVITE_TEXT))
            now = time.time()
            self._state["last_invite_at"] = now
            self._bump_daily()
            self._save()
            logger.info(
                f"[autonomy] 已邀请她考虑发说说（今日第 {self._state.get('invites_today', 0)} 次）"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[autonomy] 邀请流程异常（已忽略）: {exc}")
            return False

    def mark_published(self) -> None:
        """她真的发了说说，记下来，当天不再邀请。"""
        self._state["last_publish_at"] = time.time()
        self._state["publish_date"] = self._today()
        self._save()
        logger.info("[autonomy] 她刚发了说说，今天不再提这事")

    # ---------- 判断 ----------

    def _should_invite(self, event) -> bool:
        now = time.time()

        # 同一条消息只掷一次
        mid = self._msg_id(event)
        if mid:
            if mid in self._seen:
                return False
            self._seen.append(mid)
            if len(self._seen) > self.SEEN_MSG_KEEP:
                self._seen = self._seen[-self.SEEN_MSG_KEEP :]

        if now - float(self._state.get("last_invite_at") or 0) < self.COOLDOWN_SECONDS:
            return False

        # 今天已经发过了，就别再提醒
        if self._state.get("publish_date") == self._today():
            return False

        if self._in_quiet_hours():
            return False

        if self._invites_today() >= self.DAILY_INVITE_MAX:
            return False

        return random.random() < self.PROB

    def _in_quiet_hours(self) -> bool:
        hour = datetime.now(self.timezone).hour if self.timezone else datetime.now().hour
        return self.QUIET_START <= hour < self.QUIET_END

    def _invites_today(self) -> int:
        if self._state.get("invites_date") != self._today():
            return 0
        return int(self._state.get("invites_today") or 0)

    def _bump_daily(self) -> None:
        today = self._today()
        if self._state.get("invites_date") != today:
            self._state["invites_date"] = today
            self._state["invites_today"] = 0
        self._state["invites_today"] = int(self._state.get("invites_today") or 0) + 1

    def _today(self) -> str:
        dt = datetime.now(self.timezone) if self.timezone else datetime.now()
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _msg_id(event) -> str:
        try:
            obj = getattr(event, "message_obj", None)
            return str(getattr(obj, "message_id", "") or "")
        except Exception:
            return ""

    # ---------- 状态持久化 ----------

    def _load(self) -> dict:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug(f"[autonomy] 状态读取失败，重置: {exc}")
        return {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"[autonomy] 状态写入失败: {exc}")
