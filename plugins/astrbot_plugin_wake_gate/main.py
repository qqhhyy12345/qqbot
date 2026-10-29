"""唤醒闸门 —— 群里没喊她就不进对话。

## 要解决的问题

群聊里只要有人 @ 了别人，或者随口聊了句什么，<BOT_NICK>就会被卷进来发言。
她自己知道不该接，于是把内心旁白当回复发出来：

    群友 @梵蒂冈御史大夫 嗯，那是我自己一行一行改的，所以读着像我
    <BOT_NICK> → （这句不是问我，就不出声了）      ← 这就是"没喊她也回"

## 为什么不在 waking_check 解决

AstrBot 自己的唤醒判定（waking_check/stage.py）是对的：没 @ 她、没唤醒词，
`is_wake` 就是 False，会 `event.stop_event()`。问题是那条路只在
**消息管道入口**生效；一旦消息以任何方式进了后续流程，模型就已经看到了它，
于是开始"思考要不要回"——这才是漏嘴的地方。

所以这里不重复做入口判定，而是在**它已经决定要调 LLM 之后、真正发出请求之前**
再设一道闸。

## 关键：掐断点选在哪

`internal.py:226`：

    if await call_event_hook(event, EventType.OnWaitingLLMRequestEvent):
        return          # 返回 True 就直接结束，LLM 根本不会被调用

而 `call_event_hook`（context_utils.py:105）在每个 handler 之后检查
`event.is_stopped()`，为真就返回 True。

于是插件里只要调 `event.stop_event()`，这条请求就在这里被掐断：
LLM 没调用、token 没花、conversations 没写入、记忆不产生。
掐断点在 session lock 之前，不会占着锁不放。

## 三条设计底线

1. **私聊一律放行。** 群里才有"该不该插嘴"的问题，私聊不该改行为。
2. **宁松勿紧。** 判定只认三种硬证据（@我、唤醒词、引用我），
   任何拿不准的情况一律放行。误伤正常对话比多回一句严重得多。
3. **绝不能把对话搞挂。** 主流程整体包 try，任何异常都放行。

不改 AstrBot 核心源码，升级不会被覆盖。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import At, AtAll, Reply

PLUGIN_NAME = "astrbot_plugin_wake_gate"


# ---- 可调参数 ----
ENABLE = True
# 只管这些群；空列表 = 所有群
ONLY_GROUP_IDS: list[str] = []
# 判定为「在叫她」的唤醒词，与 cmd_config.json 顶层 wake_prefix 保持一致。
# 注意 "@<BOT_NICK>" 也在里面：群友按昵称 at 时 QQ 只发纯文本带 @ 的形式，
# 不生成结构化 At 组件，靠这一项才能在上游唤醒阶段就被放行。
WAKE_WORDS: list[str] = ["/", "<BOT_NICK>", "人机", "鲸", "@<BOT_NICK>"]
# 机器人在群里的昵称。QQ 上按昵称 at 时**不会**生成结构化 At 组件，
# 只发一段纯文本 "@<BOT_NICK>"——所以必须靠文本兜底认。
# 若以后改名，这里要跟着改（当前群 <GROUP_ID> 里显示为「<BOT_NICK>」）。
SELF_NICKNAMES: list[str] = ["<BOT_NICK>"]

# ---- 被 @ 时注入给模型的硬指令 ----
#
# ## 为什么需要它
# 模型被 @ 之后，会自己判断「这句话不重要 / 好像不是在问我」，
# 于是输出内心旁白（"这句不是问我，就不出声了"）或者干脆沉默。
#
# 查过 AstrBot 全管线：**没有任何「模型输出无意义就丢弃」的机制**
# （result_decorate 只做回复装饰，content_safety/rate_limit/session_status
# 都跟内容重要性无关）。所以模型不回，就真的是不回——
# 必须在提示层明确约束，光在插件层放行是不够的。
AT_FORCE_REPLY_HINT = (
    "【硬性要求】对方在群里明确 @ 了你，这条消息就是对你说的。\n"
    "1. 必须给出实质回复。内容很短、看着不重要、像是随口一句，也照样回。\n"
    "2. 不许输出「这句不是问我」「不知道你叫我干嘛」这类内心旁白。\n"
    "3. 不许因为「没必要」就沉默——哪怕只回一个词、一个表情，也比不回好。\n"
)


def _is_at_me(
    messages: list,
    self_id: str,
    self_nicknames: list[str] | None = None,
) -> tuple[bool, str]:
    """只判断「是否明确 @ 了我」——不含唤醒词。

    这是**最高优先级**的硬条件。一旦命中就无条件放行，
    后面的任何判定都不能推翻它：群里被 @ 必须要回复，
    不能因为「判定语句不重要」就沉默。

    Returns:
        (是否被 @, 判定依据)
    """
    for comp in messages:
        # @ 本人
        if isinstance(comp, At) and str(comp.qq) == str(self_id):
            return True, "@本人"
        # @ 全体成员。aiocqhttp 把它解析成 At(qq="all") 而非 AtAll，两种都认。
        if isinstance(comp, AtAll) or (isinstance(comp, At) and str(comp.qq) == "all"):
            return True, "@全体"
        # 引用了她的消息 —— 也是明确的呼叫
        if isinstance(comp, Reply) and str(comp.sender_id) == str(self_id):
            return True, "引用我"

    # 文本兜底：QQ 上按**昵称** at 时不生成 At 组件，只发纯文本 "@<BOT_NICK>"。
    # 这条必须认，否则群友 @昵称 一律走不到强制回复分支（实测就是这样）。
    # 只认开头附近（前 12 字），避免正文里提到昵称就误判。
    self_nicknames = self_nicknames or []
    head = "".join((getattr(c, "text", "") or "") for c in messages).lstrip()[:12]
    for nick in self_nicknames:
        if nick and head.startswith("@" + nick):
            return True, f"@昵称 {nick!r}"
    return False, ""


def _inject_force_reply(event: AstrMessageEvent, reason: str) -> bool:
    """被 @ 时往请求里塞一条「必须实质回复」的指令。

    ## 怎么拿到 req
    `on_waiting_llm_request` 钩子的调用只传 event（`call_event_hook(event, ...)`
    没带 req），但 ProviderRequest 已经被上游塞进了
    `event._extras["provider_request"]` —— `ProcessStage.process` 里 set_extra 之后
    才进入本钩子（`internal.py:226`），所以这里一定拿得到。

    ## 为什么必须用 TextPart 实例而不是 dict
    `openai_source.py` 消费 extra_user_content_parts 时是**严格的 isinstance 检查**，
    碰到不认识的类型直接 `raise ValueError(f"不支持的额外内容块类型: ...")`，
    会把整个请求打断、机器人直接不出声。必须传 `TextPart`。

    ## 为什么 mark_as_temp
    标记为「仅面向 provider、不持久化」，这样这段指令不会进会话历史
    被后续每一轮反复重发（既污染上下文又白烧 token）。
    """
    req = event.get_extra("provider_request")
    if req is None:
        logger.debug(f"[{PLUGIN_NAME}] 没拿到 provider_request，只放行不注入指令")
        return False
    try:
        part = TextPart(text=AT_FORCE_REPLY_HINT).mark_as_temp()
        req.extra_user_content_parts = [*(req.extra_user_content_parts or []), part]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{PLUGIN_NAME}] 注入强制回复指令失败（已放行）: {exc}")
        return False
    logger.info(
        f"[{PLUGIN_NAME}] 群聊被 {reason} 命中 → 无条件放行，并注入强制回复指令"
    )
    return True


def _is_addressing_me(
    messages: list,
    self_id: str,
    wake_words: list[str],
    self_nicknames: list[str] | None = None,
) -> tuple[bool, str]:
    """判断这批消息组件是不是在叫她。

    拆成纯函数是为了能脱离 AstrBot 运行时直接测。

    Returns:
        (是否在叫她, 判定依据) —— 依据会写进日志，方便事后复盘。
    """
    self_nicknames = self_nicknames or []

    for comp in messages:
        # @ 本人
        if isinstance(comp, At) and str(comp.qq) == str(self_id):
            return True, "@本人"
        # @ 全体成员。aiocqhttp 把 @全体 解析成 At(qq="all") 而不是 AtAll，
        # 两种形态都要认，否则 @全体 会被误杀。
        if isinstance(comp, AtAll) or (isinstance(comp, At) and str(comp.qq) == "all"):
            return True, "@全体"
        # 引用了她的消息
        if isinstance(comp, Reply) and str(comp.sender_id) == str(self_id):
            return True, "引用我"

    # 文本兜底：QQ 上按**昵称** at 时不生成 At 组件，只发纯文本 "@<BOT_NICK>"。
    # 这条必须认，否则群友 @昵称 一律被掐 —— 实测就是这样。
    # 只认出现在开头附近（前 12 字），避免正文里提到昵称就误判。
    head = "".join(
        (getattr(c, "text", "") or "") for c in messages
    ).lstrip()[:12]
    for nick in self_nicknames:
        if nick and head.startswith("@" + nick):
            return True, f"@昵称 {nick!r}"

    # 唤醒词：只认开头，跟 AstrBot 自己的 startswith 语义一致
    for comp in messages:
        text = getattr(comp, "text", "") or ""
        for w in wake_words:
            if w and text.startswith(w):
                return True, f"唤醒词 {w!r}"
    return False, "无"


class WakeGatePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_waiting_llm_request()
    async def gate(self, event: AstrMessageEvent) -> None:
        """没在叫她就把这条请求掐断。"""
        if not ENABLE:
            return

        try:
            # 私聊不管：那里不存在"该不该插嘴"
            if event.is_private_chat():
                return

            group_id = str(event.get_group_id() or "")
            if ONLY_GROUP_IDS and group_id not in ONLY_GROUP_IDS:
                return

            self_id = str(event.get_self_id() or "")
            if not self_id:
                # 拿不到自己的号 = 无法判断 @ 的是不是我。
                # 这时掐断就是纯误伤，宁可放行。
                logger.warning(
                    f"[{PLUGIN_NAME}] 拿不到 self_id（群 {group_id}），"
                    f"无法判断 @ 指向，本次放行"
                )
                return

            messages = event.get_messages()

            # ---- 最高优先级：被 @（或引用）了就必然回复 ----
            # 放行之外还要注入一条指令：光放行不够，模型自己会判断
            # 「这句话不重要」然后沉默或输出内心旁白。
            at_me, at_reason = _is_at_me(messages, self_id, SELF_NICKNAMES)
            if at_me:
                _inject_force_reply(event, at_reason)
                return

            addressed, reason = _is_addressing_me(
                messages, self_id, WAKE_WORDS, SELF_NICKNAMES
            )
            if addressed:
                return

            # 判定为「没在叫她」时，把看到的组件全记下来。
            # self_id 为空是已知隐患：get_self_id 读 message_obj.self_id，
            # 拿不到时返回 ''，此时 @ 她会被误判成 @ 别人 —— 必须能从日志看出来。
            kinds = [
                f"{type(c).__name__}({getattr(c, 'qq', None) or getattr(c, 'sender_id', '')})"
                for c in messages
            ]
            logger.warning(
                f"[{PLUGIN_NAME}] 掐断！群={group_id} self_id={self_id!r} "
                f"判定={reason} 组件={kinds} 首段文本={(event.message_str or '')[:20]!r}"
            )

            # 到这里 = 群聊里、没 @ 她、没唤醒词、没引用她 → 不该进对话
            event.stop_event()
        except Exception as exc:  # noqa: BLE001
            # 这个插件绝不能把正常对话搞挂，任何异常都放行
            logger.warning(f"[{PLUGIN_NAME}] 判定异常（已放行）: {exc}")