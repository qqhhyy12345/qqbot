"""出站净化 —— 拦下模型偶尔复现的内部标记。

背景：AstrBot 会把 <system_reminder>...</system_reminder> 追加进**用户消息**的
content parts（astr_main_agent.py:1035，走 extra_user_content_parts），而不是
system role。模型绝大多数时候能正确忽略它，但在聊到身份、记忆这类 meta 话题时
偶尔会把它当成正文复现出来；而出站路径（aiocqhttp 适配器）没有任何过滤。

本插件挂在「发送消息前」钩子（on_decorating_result）上做最后一道兜底：
不管模型怎么抽风，带内部标记的文本都发不出去。

不改 AstrBot 核心代码，升级不会被覆盖。
"""

from __future__ import annotations

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_no_leak"

# 标签写法放宽到 system_reminder / system-reminder / system reminder 三种变体
_TAG_OPEN = r"<system[_\-\s]?reminder[^>]*>"
_TAG_CLOSE = r"</system[_\-\s]?reminder[^>]*>"

# 成对标签，允许跨行
_REMINDER_BLOCK = re.compile(
    _TAG_OPEN + r".*?" + _TAG_CLOSE,
    re.DOTALL | re.IGNORECASE,
)

# 落单的开标签：正常输出不会有开无闭，后面全是要藏的内容，删到结尾
_REMINDER_OPEN = re.compile(
    _TAG_OPEN + r".*\Z",
    re.DOTALL | re.IGNORECASE,
)

# 落单的闭标签：只删标签本身，前面的文本可能是正常回复
_REMINDER_CLOSE = re.compile(_TAG_CLOSE, re.IGNORECASE)

# 兜底：模型可能把元信息整行复现出来而不带任何标签
_META_LINE = re.compile(
    r"^[ \t]*(?:Current datetime:|User ID:[^\n]*Nickname:|Group name:)[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    """剥掉内部标记，返回清理后的文本。"""
    cleaned = _REMINDER_BLOCK.sub("", text)   # 先处理成对的
    cleaned = _REMINDER_OPEN.sub("", cleaned)  # 再处理落单开标签（删到尾）
    cleaned = _REMINDER_CLOSE.sub("", cleaned)  # 落单闭标签只删标签
    cleaned = _META_LINE.sub("", cleaned)      # 最后兜裸露的元信息行
    return cleaned.strip()


class NoLeakPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_decorating_result()
    async def strip_internal_tags(self, event: AstrMessageEvent):
        """发送前把内部标记剥干净；整条都是标记就直接拦下。"""
        result = event.get_result()
        if result is None or not result.chain:
            return

        hits = 0
        for comp in result.chain:
            if not isinstance(comp, Plain):
                continue
            original = comp.text or ""
            cleaned = _sanitize(original)
            if cleaned != original:
                comp.text = cleaned
                hits += 1

        if not hits:
            return

        # 剥完之后如果没有任何可发内容（既没文本、也没图片/at 之类），
        # 就清空 chain——stage 会因此跳过发送，不会吐出空消息。
        has_content = False
        for comp in result.chain:
            if isinstance(comp, Plain):
                if (comp.text or "").strip():
                    has_content = True
                    break
            else:
                has_content = True
                break

        if not has_content:
            result.chain.clear()
            logger.warning(f"[{PLUGIN_NAME}] 整条回复都是内部标记，已拦截不发送")
            return

        logger.info(f"[{PLUGIN_NAME}] 已剥离内部标记 {hits} 处")
