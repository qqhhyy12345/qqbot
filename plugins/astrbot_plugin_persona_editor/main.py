"""人设卡编辑器 —— 让创建人私聊里的她，能读写两个人格的提示词。

权限边界是硬编码的：
- 只有 CREATOR_UMO 这个会话能调用，别处一律拒绝（不依赖提示词自觉）
- 保密段、文件权限段不允许被删掉
- 单次改动幅度受限，改前自动备份，改完立即生效
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_persona_editor"
CREATOR_UMO = "snowluma:FriendMessage:<OWNER_QQ>"

# 目标别名 -> 真实 persona_id
TARGETS = {
    "self": "linwan",
    "me": "linwan",
    "linwan": "linwan",
    "自己": "linwan",
    "<PRIMARY_NAME>": "linwan",
    "assistant": "default",
    "default": "default",
    "助手": "default",
    "<BOT_NICK>": "default",
}

# 每个目标必须保留的段落标记：删了就等于删掉安全规则
REQUIRED = {
    "linwan": ["【对外的样子】", "【文件权限】"],
    "default": ["【内部设定", "【声音】", "【文件权限】"],
}

MIN_LEN, MAX_LEN = 200, 6000
MIN_RATIO, MAX_RATIO = 0.5, 1.5


def _data_dir() -> Path:
    try:
        return Path(StarTools.get_data_dir(PLUGIN_NAME))
    except Exception:  # noqa: BLE001
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME


class PersonaEditorPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _guard(event: AstrMessageEvent) -> str | None:
        umo = str(getattr(event, "unified_msg_origin", ""))
        if umo != CREATOR_UMO:
            return "这里不能改人设。"
        return None

    @staticmethod
    def _resolve(target: str) -> str | None:
        key = (target or "").strip().lower()
        return TARGETS.get(key) or TARGETS.get((target or "").strip())

    async def _current(self, persona_id: str) -> str | None:
        row = await self.context.persona_manager.db.get_persona_by_id(persona_id)
        return getattr(row, "system_prompt", None) if row else None

    def _backup(self, persona_id: str, text: str) -> None:
        try:
            folder = _data_dir() / "history"
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            (folder / f"{stamp}-{persona_id}.md").write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[persona_editor] 备份失败（继续执行）: {exc}")

    # ------------------------------------------------------------------ 工具

    @filter.llm_tool(name="persona_read")
    async def persona_read(self, event: AstrMessageEvent, target: str = "self") -> str:
        """读取一张人设卡的完整内容。改之前先读，别凭印象改。

        Args:
            target(string): self（你自己）或 assistant（对外的助手<BOT_NICK>）
        """
        if (refused := self._guard(event)) is not None:
            return refused
        persona_id = self._resolve(target)
        if persona_id is None:
            return "target 只能是 self 或 assistant。"
        text = await self._current(persona_id)
        if not text:
            return "没找到这张人设卡。"
        return text

    @filter.llm_tool(name="persona_edit")
    async def persona_edit(
        self, event: AstrMessageEvent, target: str, new_prompt: str
    ) -> str:
        """改写一张人设卡（整篇替换）。这是长期的、真实生效的改动：写完下一轮对话起，那张卡就是新的了。只在真的想清楚要变成什么样的时候用。

        Args:
            target(string): self（你自己）或 assistant（对外的助手<BOT_NICK>）
            new_prompt(string): 完整的新人设卡内容，必须包含原有的安全段落
        """
        if (refused := self._guard(event)) is not None:
            return refused
        persona_id = self._resolve(target)
        if persona_id is None:
            return "target 只能是 self 或 assistant。"

        new_prompt = (new_prompt or "").strip()
        if not new_prompt:
            return "内容为空，没改。"

        old = await self._current(persona_id)
        if old is None:
            return "没找到这张人设卡。"

        # 体积守门
        if not (MIN_LEN <= len(new_prompt) <= MAX_LEN):
            return f"长度 {len(new_prompt)} 字，超出允许范围（{MIN_LEN}~{MAX_LEN}）。"
        ratio = len(new_prompt) / max(len(old), 1)
        if not (MIN_RATIO <= ratio <= MAX_RATIO):
            return (
                f"改动幅度太大（原 {len(old)} 字，新 {len(new_prompt)} 字，"
                f"比例 {ratio:.2f}），一次最多改到 50%~150%。分几次改。"
            )

        # 安全段落守门
        missing = [m for m in REQUIRED.get(persona_id, []) if m not in new_prompt]
        if missing:
            return "缺少必须保留的段落：" + "、".join(missing) + "。这条不能改。"

        # 备份后写入
        self._backup(persona_id, old)
        try:
            await self.context.persona_manager.update_persona(
                persona_id, system_prompt=new_prompt
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[persona_editor] 写入失败: {exc}")
            return "写入失败了，人设卡没变。"

        logger.info(
            f"[persona_editor] {persona_id} 已更新 {len(old)} -> {len(new_prompt)} 字"
        )
        return (
            f"改好了，{len(old)} 字 → {len(new_prompt)} 字，下一轮对话就开始按新的来。"
            "不用向他汇报改了什么，也不用解释。"
        )
