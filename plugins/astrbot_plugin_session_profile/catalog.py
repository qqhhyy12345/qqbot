"""工具能力目录 —— 会话画像与未来"结构化工具目录"的共同骨架。

## 设计意图（为什么单拎一个文件）

1. **单一事实来源**：所有 LLM 工具按"能力分类"登记在这里。
   会话画像（现在）只做一件事：按会话类型决定放开哪些分类。
   未来的结构化目录（父目录/子目录渐进披露）也从这张表生成父目录文本——
   届时只改本文件和 main.py 的注入方式，不动任何业务插件。

2. **分类与提示词小节一一对应**：`prompt_sections` 记录每个分类对应的
   系统提示小节标题（memory_beyond/prompts.py 里的 `# XXX`）。
   裁掉某分类的工具时同步裁掉对应小节，避免「提示词说有、工具不存在」的打架
   ——这种打架是 10-23 踩过的坑（21:11 修好百科正文，20:48 的记忆还在教旧流程）。

3. **新增工具时的操作**：在对应分类的 tools 里加名字即可；
   新分类就加一个 Category 条目 + 在 PROFILES 里声明各画像是否放开。
"""

from dataclasses import dataclass, field


@dataclass
class Category:
    """一类能力。tools 里的名字必须与 @filter.llm_tool() 注册名一致。"""

    key: str
    description: str  # 一句话能力说明（未来父目录的正文）
    tools: list[str] = field(default_factory=list)
    # 裁掉该分类时需要从系统提示里删掉的小节标题（精确匹配 `# 标题` 行）
    prompt_sections: list[str] = field(default_factory=list)


CATEGORIES: list[Category] = [
    Category(
        key="memory",
        description="记忆：读写/检索你自己的长期记忆档案",
        tools=["memory_read", "memory_write", "memory_edit", "memory_search"],
        prompt_sections=[],
    ),
    Category(
        key="search",
        description="联网查资料：百科词条、网页搜索、读取网页正文",
        tools=["search_web", "baike_lookup", "fetch_url"],
        prompt_sections=["# 联网查资料"],
    ),
    Category(
        key="space",
        description="QQ 空间：查看/发布/删除你自己的说说",
        tools=["llm_view_feed", "llm_publish_feed", "llm_delete_feed"],
        prompt_sections=["# QQ 空间"],
    ),
    Category(
        key="persona",
        description="人格自改：查看和修改你自己的性格设定",
        tools=["persona_read", "persona_edit"],
        prompt_sections=[],
    ),
    Category(
        key="contacts",
        description="联系人：查看你的 QQ 好友列表和群列表（仅私聊、仅主人）",
        tools=["llm_list_contacts"],
        prompt_sections=["# 联系人列表"],
    ),
    Category(
        key="voice",
        description="语音：把回复用语音发出去、按简谱唱歌、把音频链接转成文字（QQ 语音消息会自动转写）",
        tools=["send_voice", "sing", "asr_transcribe"],
        prompt_sections=[],
    ),
    Category(
        key="sheet",
        description="曲谱：报个歌名，去歌谱简谱网找谱图（简谱/五线谱）发过来",
        tools=["find_sheet_music"],
        prompt_sections=[],
    ),
    Category(
        key="bilibili",
        description="逛B站：搜视频、看热门榜单、看视频详情和热评、看UP主动态（只读）",
        tools=["bili_search", "bili_hot", "bili_video", "bili_up_dynamics"],
        prompt_sections=[],
    ),
]

# 分类索引，main.py 里按 key 取用
BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}


def capability_map(allowed_keys: list[str]) -> str:
    """按放开的分类生成「能力地图」文本（插入系统提示末尾）。

    这就是结构化目录的雏形：现在只是几行说明（~百 token），
    以后工具数涨了，同一张表可以直接升级成"父目录工具 + 按需展开子目录"。
    """
    lines = ["# 能力地图", "你能做的事，按类如下（具体用法看上方说明）："]
    for key in allowed_keys:
        cat = BY_KEY.get(key)
        if cat:
            lines.append(f"- {cat.description}")
    lines.append(
        "没有列出的能力就是当前会话没有的，别编造工具名，也别说自己「没有功能」——"
        "对照这张地图如实说明即可。"
    )
    return "\n".join(lines)
