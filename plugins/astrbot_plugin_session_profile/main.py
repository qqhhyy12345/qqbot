"""会话画像 —— 按会话类型裁剪工具与提示词，并注入能力地图。

## 要解决的问题

所有 LLM 工具（14 个，定义 ~3.5K token）和全部工具使用规则（系统提示里的
「# QQ 空间」「# 联系人列表」等小节）会被**全量下发到每一个会话**：
群聊里她也背着发说说/删说说/查好友的完整定义和说明——
既是固定 token 死重，也是权限问题（群聊本就不该看到这些能力）。

## 做什么

挂 `on_waiting_llm_request` 钩子（与 wake_gate 同一位置，且在其放行之后才会
走到这里），按会话类型分三档画像：

- owner  ：主人的私聊 → 全量
- friend ：其他人的私聊 → 裁掉联系人目录（只有主人用得上）
- group  ：群聊 → 再裁掉 QQ 空间、人格自改

每个画像做三件事：
1. 从 req.func_tool 里移除不放开的分类的工具（ToolSet 每请求新建，改动不外泄，
   已验证 `get_full_tool_set()` 每次构造新实例）；
2. 从 req.system_prompt 里删掉对应分类的说明小节（防止「提示词说有、工具没有」）；
3. 在系统提示末尾注入「# 能力地图」（见 catalog.py，未来结构化目录的雏形）。

## 三条设计底线（与 wake_gate 一致）

1. 拿不准的会话一律按最宽画像处理（宁多给不误伤）。
2. 主流程整体 try，任何异常都原样放行，绝不因为画像挂掉而阻断对话。
3. 不改 AstrBot 核心源码，升级不被覆盖。

## 已验证的关键事实（勿凭记忆推翻）

- `req.func_tool` 由 `astr_main_agent.py:665` 每请求新建（get_full_tool_set），
  钩子里 remove_tool 只影响本次请求。
- runner 每轮通过 `_func_tool_for_provider()` 读 `req.func_tool`（tool_loop_agent_runner.py:687），
  钩子在 runner 启动前执行，裁剪对整个 tool-loop 生效。
- 钩子只传 event，req 从 `event.get_extra("provider_request")` 取（上游已 set_extra）。
"""

from __future__ import annotations

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .catalog import BY_KEY, capability_map

PLUGIN_NAME = "astrbot_plugin_session_profile"

# 主人 QQ 号（与人格-<PRIMARY_NAME>.md、wake_gate 的分流一致）
OWNER_ID = "<OWNER_QQ>"

# 各画像放开的分类（key 见 catalog.py）。没列出的分类 = 该会话裁掉。
PROFILES: dict[str, list[str]] = {
    "owner": ["memory", "search", "space", "persona", "contacts", "voice", "sheet"],
    "friend": ["memory", "search", "voice", "sheet"],
    "group": ["memory", "search", "voice", "sheet"],
}

_SECTION_HEAD = re.compile(r"^# .+$", re.MULTILINE)


def _detect_profile(event: AstrMessageEvent) -> str:
    """按会话判画像。拿不准一律最宽（owner 只认主人私聊）。"""
    try:
        if event.get_group_id():
            return "group"
        if str(event.get_sender_id()) == OWNER_ID:
            return "owner"
        return "friend"
    except Exception:
        return "owner"


def _strip_sections(system_prompt: str, dropped_keys: list[str]) -> str:
    """从系统提示里删掉被裁分类对应的小节。

    小节以 `# 标题` 行开头，到下一个 `# ` 行或文本结尾。
    只删 catalog 里登记过的标题，别的一律不碰。
    """
    drop_titles: set[str] = set()
    for key in dropped_keys:
        cat = BY_KEY.get(key)
        if cat:
            drop_titles.update(cat.prompt_sections)
    if not drop_titles:
        return system_prompt

    heads = list(_SECTION_HEAD.finditer(system_prompt))
    if not heads:
        return system_prompt

    # 计算每个小节的起止区间
    ranges_to_drop: list[tuple[int, int]] = []
    for i, m in enumerate(heads):
        title = m.group(0).strip()
        if title in drop_titles:
            start = m.start()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(system_prompt)
            ranges_to_drop.append((start, end))

    if not ranges_to_drop:
        return system_prompt

    out, last = [], 0
    for start, end in ranges_to_drop:
        out.append(system_prompt[last:start])
        last = end
    out.append(system_prompt[last:])
    result = "".join(out)
    # 清理可能产生的连续空行
    return re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"


class SessionProfilePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_waiting_llm_request()
    async def profile(self, event: AstrMessageEvent) -> None:
        try:
            req = event.get_extra("provider_request")
            if req is None:
                return
            profile = _detect_profile(event)
            allowed = PROFILES.get(profile, PROFILES["owner"])
            dropped = [k for k in BY_KEY if k not in allowed]

            # 1) 裁工具
            tools = getattr(req, "func_tool", None)
            removed: list[str] = []
            if tools is not None:
                present = set(tools.names())
                for key in dropped:
                    for name in BY_KEY[key].tools:
                        if name in present:
                            tools.remove_tool(name)
                            removed.append(name)

            # 2) 裁提示小节 + 3) 注入能力地图
            sp = getattr(req, "system_prompt", "") or ""
            if dropped:
                sp = _strip_sections(sp, dropped)
            sp = sp.rstrip() + "\n\n" + capability_map(allowed) + "\n"
            req.system_prompt = sp

            if removed:
                logger.debug(
                    f"[{PLUGIN_NAME}] profile={profile} 裁掉工具: {removed}"
                )
        except Exception as e:
            # 画像失败绝不阻断对话
            logger.warning(f"[{PLUGIN_NAME}] 处理失败（已放行）: {e}")
