"""分层记忆 —— 把过老的对话流水归档成持久摘要，只保留近期原文。

## 解决的问题

AstrBot 每次请求都会把 conversations 里的完整历史读出来处理，但内置的压缩
（ContextManager / LLMSummaryCompressor）是**一次性**的：压完只用于本次请求，
不回写任何地方。结果是每轮都在重处理同一批老历史，摘要也不留档。

## 做法

挂 on_llm_request 钩子，在请求发出前改写 contexts：

- L0 近期原文：最近 KEEP_RECENT_ROUNDS 轮，一字不改
- L1 持久摘要：更老的轮次压成摘要，**落盘**到 bot-memory/archive/，只压一次
- 游标持久化，下次请求直接读摘要，不重算

## 安全边界

- **绝不改动 conversations 表**。只改"本次请求读到的东西"，原文一个字节不碰，
  随时可回滚，删掉插件即恢复原状。
- 摘要生成失败 = 什么都不改（保留原文），绝不让归档逻辑阻塞回复。
- 限频：同一会话 COOLDOWN_SECONDS 内只归档一次。
- 近期轮数不足 KEEP_RECENT_ROUNDS 时不动手。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_memory_tier"

# ---- 可调参数 ----
KEEP_RECENT_ROUNDS = 15      # 最近多少轮保留原文
SOFT_LIMIT_TOKENS = 22000    # 上下文估算超过这个值才考虑归档（真实口径）
MAX_ARCHIVE_ROUNDS = 30      # 单次最多归档多少轮（控制摘要请求本身的大小）
COOLDOWN_SECONDS = 300       # 同一会话两次归档的最小间隔
IMAGE_TOKENS = 1500          # 一张 base64 图片按视觉口径粗估（对齐 AstrBot 的 IMAGE_TOKEN_ESTIMATE）

# 归档时是否剥掉保留轮的推理痕迹（think）
#
# 为什么可以剥：归档摘要本身就不收 think（`_text_of` 只取 text），
# 所以 think 在这套架构里**早就是可丢弃的东西** —— 被归档的老轮次早就丢了它，
# 保留轮留着反而是不一致。实测 think 在当前上下文里占 27%~57%（中位约 42%）。
#
# 为什么只能在归档时做：每轮单独改 contexts → prompt 前缀每轮都变 → 缓存全废
# （实测归档速率上去后缓存命中率 85.7% → 63.2%，折算成本 +77%）。
# 归档本来就要重写 contexts，顺手剥一次**不额外增加缓存失效次数**。
STRIP_THINK = True

# ---- A 方案：累计摘要合并压缩 ----
#
# ## 为什么要有这个
# 旧实现里每次归档都往归档文件**追加**一个摘要块，而指针块每次都把
# **整个文件**拼进请求。实测私聊归档文件已 25 个块 / 5,809 字符 = **3,861 token**，
# 占上下文的 18%，而且**每次归档都让它更长、从不淘汰**。
#
# ## 现在的做法
# 归档文件拆成两个：
#   - `<name>.md`        = **总摘要**（覆盖式，全历史压成一个，有恒定长度上限）
#   - `<name>.blocks.md` = 原始块存档（追加式，只作存档，不全量进请求）
# 指针内容 = 总摘要 + 最近 KEEP_RECENT_SUMMARY_BLOCKS 个原始块。
#
# ## 为什么还要留最近几块
# 反复合并会有**摘要递减**（lossy chaining）：压一次丢一点细节。
# 带上最近几块原文，近期细节不丢；KEEP_RECENT_SUMMARY_BLOCKS = 0 就是纯合并。
KEEP_RECENT_SUMMARY_BLOCKS = 3
TOTAL_SUMMARY_TARGET_CHARS = 500   # 总摘要的目标长度（写进提示词）

# token 换算系数 —— 由 tools/measure_tokens.py 用方舟 API 的 usage.prompt_tokens 实测，
# 不是拍脑袋的。历史版本用 0.7 / 0.3，低估 48%，导致归档触发晚了近一倍。
CJK_TOKEN_COEF = 1.06        # 一个汉字 ≈ 1.06 token
MIXED_TOKEN_COEF = 0.55      # 英文 / URL / JSON 混排的字符
ONLY_UMO: list[str] = []  # 空列表 = 所有会话生效（新群、新私聊自动享受分层归档）

MERGE_PROMPT = (
    "你是对话归档员。下面是一段长期对话的累计记忆摘要，"
    "以及刚刚新归档的一段摘要。请把它们合并成一份简洁但完整的长期记忆摘要。\n"
    "要求：\n"
    "1. **绝对不能丢**：用户的身份和称呼、我们之间的约定和规则、"
    "正在进行的事和待办、已经确定的结论、承诺过的事；\n"
    "2. 重复的内容合并成一条，旧的细节让位给新的；\n"
    "3. 丢掉寒暄、重复、语气词和过程描述；\n"
    "4. 按时间顺序，用短句，不要分点符号，不要标题；\n"
    "5. 控制在 {target} 字以内；\n"
    "6. 用中文写；\n"
    "\n"
    "【已有累计摘要】\n{old}\n\n"
    "【新归档摘要】\n{new}\n"
)

# 提示词占位符替换用的哨兵 —— 用 \x00 保证不可能出现在正文里
_SENTINEL_OLD = "\x00OLD\x00"
_SENTINEL_NEW = "\x00NEW\x00"
_SENTINEL_TGT = "\x00TGT\x00"

SUMMARY_PROMPT = (
    "你是对话归档员。下面是一段较早的聊天记录，请把它压缩成简洁的记忆摘要。\n"
    "要求：\n"
    "1. 保留事实、约定、在做的事、情绪基调的变化，以及提到的具体人和细节；\n"
    "2. 丢掉寒暄、重复、语气词；\n"
    "3. 按时间顺序，用短句，不要分点符号，不要标题；\n"
    "4. 控制在 400 字以内；\n"
    "5. 用中文写；\n"
    "6. 只输出摘要正文，不要任何额外说明。"
)


def _archive_dir() -> Path:
    """归档目录：bot-memory/archive/"""
    root = Path(__file__).resolve().parents[3]  # data/plugins/<plugin> -> astrbot/
    cand = root / ".." / "bot-memory" / "archive"
    return cand.resolve()


def _safe_name(umo: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in umo)


def _text_of(msg: dict) -> str:
    """从 OpenAI 格式消息里取纯文本。"""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return ""


def _est_tokens(text: str) -> int:
    """粗估 token。

    系数是**实测**出来的，不是猜的（`tools/measure_tokens.py`
    用方舟 API 的 `usage.prompt_tokens` 反推）：

    | 文本类型               | 实测系数      |
    |------------------------|---------------|
    | 纯中文（汉字）          | **1.06**/字   |
    | URL / JSON 混排        | 0.79 ~ 0.86  |
    | 纯英文句子             | 约 0.3        |

    历史版本这里是 CJK 0.7 / 其他 0.3，**低估约 48%**——
    于是阈值 20,000 实际要真实 ~38,000 token 才触发归档，
    上下文冲到 47K 才被压下去。混排字符取 0.55（介于纯英文与 URL 之间）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * CJK_TOKEN_COEF + (len(text) - cjk) * MIXED_TOKEN_COEF)


def _count_tokens(contexts: list[dict]) -> int:
    """估算整段上下文的 token —— 必须和 AstrBot 的 EstimateTokenCounter 同口径。

    ## 为什么不能只数 text

    conversations 里的 content 是 part 列表，除了 text 还有：
    - `think`：推理痕迹。AstrBot 的 openai_source 会把它作为 reasoning_content
      回传给模型（源码注释：推理模型需要 reasoning 历史），所以是真花钱的。
      实测 linwan 私聊里 think 占 13,805 token，比正文还大。
    - `image_url`：base64 图片，一张约 1,500 token。

    早期版本只数 text，导致 11,604 < 20,000 阈值，归档逻辑一遍都没真正跑过。
    """
    total = 0
    for msg in contexts:
        content = msg.get("content")
        if isinstance(content, str):
            total += _est_tokens(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                total += _est_tokens(str(part.get("text") or ""))
            elif ptype == "think":
                total += _est_tokens(str(part.get("think") or ""))
            elif ptype == "image_url":
                total += IMAGE_TOKENS
    return total


def _strip_think(msgs: list[dict]) -> tuple[list[dict], int]:
    """剥掉消息里的推理痕迹（think part）。

    返回 (新消息列表, 剥掉的 token 数)。

    ## 边界处理

    - `content` 是 str 的消息原样保留（tool 结果、纯文本消息都是这种）
    - 剥完 content 变空的消息 → content 置 ""，**不删消息**：
      删了会破坏 user/assistant 的配对；
      带 `tool_calls` 的消息一删，工具调用记录就断了
    - 用新 dict 而不是原地改：避免污染调用方持有的引用
    """
    out: list[dict] = []
    saved = 0
    for msg in msgs:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(dict(msg))
            continue
        kept: list = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "think":
                saved += _est_tokens(str(part.get("think") or ""))
                continue
            kept.append(part)
        new = dict(msg)
        new["content"] = kept if kept else ""
        out.append(new)
    return out, saved


def _fixed_overhead(req: Any, contexts: list[dict]) -> int:
    """估算「每轮都要付、与历史多长无关」的固定开销。

    只数 contexts 会漏掉两块，而它们**每次请求都全量发送**：
      - `system_prompt`：人格卡 + 系统提示
      - `func_tool`：所有 LLM 工具的定义（fetch_url 的工具说明就有 2KB+）

    实测这两块合计约 5,459 token——比单次 memory_read 的返回还大。
    漏掉它就是 20,000 token 的误差来源之一。
    """
    total = _est_tokens(getattr(req, "system_prompt", "") or "")
    tools = getattr(req, "func_tool", None)
    if tools is not None:
        try:
            if hasattr(tools, "model_dump"):
                blob = json.dumps(tools.model_dump(), ensure_ascii=False, default=str)
            elif hasattr(tools, "tools"):
                blob = json.dumps(
                    [t.model_dump() if hasattr(t, "model_dump") else str(t)
                     for t in tools.tools],
                    ensure_ascii=False, default=str,
                )
            else:
                blob = str(tools)
        except Exception:  # noqa: BLE001
            blob = str(tools)
        total += _est_tokens(blob)
    # 本轮新加的附加内容块（系统提醒、extra_user_content_parts 等）
    for part in getattr(req, "extra_user_content_parts", None) or []:
        if isinstance(part, dict):
            total += _est_tokens(str(part.get("text") or ""))
        else:
            total += _est_tokens(str(part))
    return total


def _estimate_request_tokens(req: Any, contexts: list[dict]) -> int:
    """这次请求真实会发多少 token —— 优先采信 API 的真实值。

    ## 为什么不再只信自己估的

    估算再准也是估。`req.conversation.token_usage` 存着**上一次 LLM 响应
    返回的真实 usage**（AstrBot 在 `tool_loop_agent_runner` 里每次响应都会
    覆盖它，`self.req.conversation.token_usage = llm_response.usage.total`），
    这是唯一可靠的基准。而且它是**内存对象**，不依赖数据库落盘。

    取 `max(真实值, 估算值)`：
      - 首次对话还没有真实值 → 纯估算（此时估算已按实测系数修正过）
      - 之后 → 至少不低于上一次的真实开销，估算只负责补上本轮新增的部分

    ## 会不会「归档后还一直判超限」

    不会。归档把 contexts 裁到 `system + 指针 + 15 轮`，直接触发
    `_maybe_tier` 开头的 `len(contexts) < KEEP_RECENT_ROUNDS * 2` 提前返回；
    真实值也会在下一次响应时被裁剪后的用量覆盖。
    """
    est = _count_tokens(contexts) + _fixed_overhead(req, contexts)
    conv = getattr(req, "conversation", None)
    try:
        real = int(getattr(conv, "token_usage", 0) or 0)
    except (TypeError, ValueError):
        real = 0
    if real < 0:
        real = 0
    return max(est, real)


def _split_rounds(contexts: list[dict]) -> list[list[dict]]:
    """按 user 消息切轮次；开头的 system 单独归入第一轮之前。"""
    rounds: list[list[dict]] = []
    carry: list[dict] = []
    cur: list[dict] = []
    for msg in contexts:
        role = msg.get("role")
        if role == "system":
            carry.append(msg)
            continue
        if role == "user":
            if cur:
                rounds.append(cur)
            cur = [msg]
        else:
            cur.append(msg)
    if cur:
        rounds.append(cur)
    if carry:
        # system 不该参与归档，单独放在最前面
        return [carry] + rounds
    return rounds


ARCHIVE_POINTER_MARK = "轮对话已归档，以下是累计摘要"
ARCHIVE_POINTER_ACK = "好，记着了。"


def _find_pointer_round(body_rounds: list[list[dict]]) -> int:
    """找出上下文开头那条「归档指针轮」的下标，找不到返回 -1。

    ## 为什么需要它

    每次归档成功后本插件会把 `req.contexts` 重写成
    `system + 归档指针 + 最近 15 轮`，而 **AstrBot 会把重写后的 contexts
    回写 conversations 表**（15:55 验证本地视觉时确认过这个行为）。

    于是下一轮进来时，指针那条 user+assistant 自己会被 `_split_rounds`
    切成第 0 轮。它不是真实对话，绝不能进摘要，否则摘要会自我引用、
    越滚越长。

    ## 为什么不能靠 archived_rounds 游标

    旧实现用 `archived_rounds`（累计归档轮数）当游标跳过已归档的部分。
    但 DB 轮数会被回写裁剪、游标却只增不减，实测已出现
    `archived_rounds=60 > 实际 49 轮` → `body_rounds[60:]` 为空 → 归档死锁。

    所以改成每次自识别当前上下文里的指针轮，不跨请求记游标。
    """
    if not body_rounds:
        return -1
    head = body_rounds[0]
    if len(head) < 2:
        return -1
    if head[0].get("role") != "user" or head[1].get("role") != "assistant":
        return -1
    if ARCHIVE_POINTER_MARK not in _text_of(head[0]):
        return -1
    if _text_of(head[1]).strip() != ARCHIVE_POINTER_ACK:
        return -1
    return 0


class MemoryTierPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._last_archive_at: dict[str, float] = {}

    # ---------- 归档文件 ----------
    def _paths(self, umo: str) -> tuple[Path, Path]:
        d = _archive_dir()
        name = _safe_name(umo)
        return d / f"{name}.md", d / f"{name}.meta.json"

    def _archive_paths(self, umo: str) -> tuple[Path, Path]:
        """总摘要文件 与 原始块存档文件。"""
        d = _archive_dir()
        name = _safe_name(umo)
        return d / f"{name}.md", d / f"{name}.blocks.md"

    def _migrate_archive_if_needed(self, umo: str) -> None:
        """旧格式 → 新格式的一次性迁移。

        旧格式：`<name>.md` 里堆着多个「## 归档块 N」段落。
        新格式：`<name>.md` 只放**总摘要**，`<name>.blocks.md` 存原始块。

        迁移方式：把旧的 `.md` **复制**成 `.blocks.md`。

        ## 为什么是「复制」而不是「改名」
        改名会制造一个**数据丢失窗口**：迁移一旦完成，主文件就不存在了，
        而总摘要要到合并成功才写回来。这中间如果摘要生成失败、归档提前 return，
        指针里就只剩最近几块 —— 前面二十几块**在上下文里消失了**。
        复制则让 `.md` 在合并成功前始终保留旧内容，任何一步失败都不丢东西。

        ## 为什么要「复制」而不是「只改读取逻辑」
        因为合并成功后 `.md` 会被总摘要覆盖。只改读取逻辑的话，
        每次归档都要重新解析整个旧文件做输入，而复制一次就 forever 分好了。

        代价是迁移那一瞬间多占一份文件空间（私聊约 15KB），可忽略。
        """
        total_path, blocks_path = self._archive_paths(umo)
        try:
            if blocks_path.exists() or not total_path.exists():
                return
            text = total_path.read_text(encoding="utf-8")
            if "## 归档块" not in text:
                return  # 已经是单块格式，当总摘要用
            blocks_path.write_text(text, encoding="utf-8")
            logger.info(
                f"[{PLUGIN_NAME}] 归档文件已复制为块存档（{blocks_path.name}），"
                f"总摘要将在下次归档合并后写入"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 归档格式迁移失败（按旧格式继续）: {exc}")

    def _read_total_summary(self, umo: str) -> str:
        """读总摘要（新格式的主文件）。"""
        total_path, _ = self._archive_paths(umo)
        try:
            if total_path.exists():
                return total_path.read_text(encoding="utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 读总摘要失败: {exc}")
        return ""

    def _write_total_summary(self, umo: str, text: str) -> None:
        """写总摘要（覆盖式）。

        ## 为什么要先备份旧内容
        如果 `_migrate_archive_if_needed` 失败（改名出错），主文件里还是
        **旧的多块格式**。这时直接覆盖写，旧的 25 个块就**永久丢了**。
        所以覆盖前先检查：主文件里还有块标题、且块存档不存在 →
        先把旧内容挪进块存档再写。
        """
        total_path, blocks_path = self._archive_paths(umo)
        try:
            total_path.parent.mkdir(parents=True, exist_ok=True)
            if total_path.exists() and not blocks_path.exists():
                old = total_path.read_text(encoding="utf-8")
                if "## 归档块" in old:
                    blocks_path.write_text(old, encoding="utf-8")
                    logger.info(
                        f"[{PLUGIN_NAME}] 覆盖总摘要前已把旧块内容挪进块存档"
                    )
            total_path.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 写总摘要失败: {exc}")

    def _read_recent_blocks(self, umo: str, n: int) -> str:
        """读最近 n 个原始块（给指针用，防摘要递减）。"""
        if n <= 0:
            return ""
        _, blocks_path = self._archive_paths(umo)
        try:
            if not blocks_path.exists():
                return ""
            text = blocks_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 读块存档失败: {exc}")
            return ""
        parts = [p for p in text.split("## 归档块 ") if p.strip()]
        if not parts:
            return ""
        recent = parts[-n:]
        return "\n\n".join("## 归档块 " + p.strip() for p in recent)

    def _build_pointer_text(self, umo: str) -> str:
        """指针块要塞进请求的正文 = 总摘要 + 最近几个原始块。"""
        parts = [
            p for p in (
                self._read_total_summary(umo),
                self._read_recent_blocks(umo, KEEP_RECENT_SUMMARY_BLOCKS),
            ) if p
        ]
        return "\n\n".join(parts)

    def _load_meta(self, umo: str) -> dict[str, Any]:
        _, meta_path = self._paths(umo)
        try:
            if meta_path.exists():
                return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 读 meta 失败（按 0 处理）: {exc}")
        return {"archived_rounds": 0, "blocks": 0}

    def _save_meta(self, umo: str, meta: dict[str, Any]) -> None:
        _, meta_path = self._paths(umo)
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 写 meta 失败: {exc}")

    def _append_archive(self, umo: str, block_no: int, rounds: int, summary: str) -> None:
        """把原始块追加进**块存档**（不再写主文件，主文件是总摘要）。"""
        _, blocks_path = self._archive_paths(umo)
        try:
            blocks_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M")
            with blocks_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n## 归档块 {block_no}（{stamp}，{rounds} 轮）\n\n{summary}\n")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 写归档块存档失败: {exc}")

    def _read_all_summaries(self, umo: str) -> str:
        """读**全部**原始块（存档全量，只用于合并时当输入，不进请求）。"""
        _, blocks_path = self._archive_paths(umo)
        try:
            if blocks_path.exists():
                return blocks_path.read_text(encoding="utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 读归档块存档失败: {exc}")
        return ""

    # ---------- 主体 ----------
    @filter.on_llm_request()
    async def tier_memory(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        try:
            await self._maybe_tier(event, req)
        except Exception as exc:  # noqa: BLE001
            # 归档是锦上添花，任何异常都不该影响正常回复
            logger.error(f"[{PLUGIN_NAME}] 归档流程异常（已忽略）: {exc}")

    async def _maybe_tier(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts: list[dict] = list(req.contexts or [])
        if len(contexts) < KEEP_RECENT_ROUNDS * 2:
            return

        umo = getattr(event, "unified_msg_origin", "") or ""
        if ONLY_UMO and umo not in ONLY_UMO:
            return
        umo_short = umo.split(":")[-1][-6:] or "?"

        tokens = _estimate_request_tokens(req, contexts)
        logger.debug(
            f"[{PLUGIN_NAME}] {umo_short}: {len(contexts)} 条消息，估算 {tokens} token"
            f"（阈值 {SOFT_LIMIT_TOKENS}）"
        )
        if tokens < SOFT_LIMIT_TOKENS:
            return

        rounds = _split_rounds(contexts)
        has_system_head = bool(rounds and rounds[0] and rounds[0][0].get("role") == "system")
        system_head = rounds[0] if has_system_head else []
        body_rounds = rounds[1:] if has_system_head else rounds

        # 保险：若上下文开头已混入内置压缩器的摘要（正常情况下本钩子先于它执行，
        # 不会出现），说明轮次索引不可靠，本轮直接放弃，宁可少归档也不切错。
        head_text = " ".join(_text_of(m) for m in contexts[:2])
        if "Our previous history conversation summary" in head_text:
            logger.debug(f"[{PLUGIN_NAME}] 检测到内置摘要，跳过本轮归档")
            return

        meta = self._load_meta(umo)

        # 自识别归档指针轮：它在上下文里，但不算真实对话，不能进摘要。
        pointer_idx = _find_pointer_round(body_rounds)
        pointer = body_rounds[pointer_idx] if pointer_idx >= 0 else None
        remaining = body_rounds[1:] if pointer is not None else body_rounds

        if len(remaining) <= KEEP_RECENT_ROUNDS:
            return

        # 限频
        now = time.time()
        last = self._last_archive_at.get(umo, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return

        to_archive = remaining[:-KEEP_RECENT_ROUNDS][-MAX_ARCHIVE_ROUNDS:]
        keep_recent = remaining[-KEEP_RECENT_ROUNDS:]
        if not to_archive:
            return

        summary = await self._summarize(event, to_archive)
        if not summary:
            logger.info(f"[{PLUGIN_NAME}] 摘要为空，本轮不改动历史")
            return

        # 迁移必须放在摘要成功之后 —— 迁移只复制不删，即使放前面也安全，
        # 但放这里逻辑更直白：确认有摘要了，才动文件结构。
        self._migrate_archive_if_needed(umo)

        # A 方案：把「旧累计摘要 + 新摘要」合并成一个总摘要，指针长度才有上限。
        # 旧摘要有两个来源：新格式的总摘要文件；旧格式迁移过来的块存档全文。
        old_total = self._read_total_summary(umo) or self._read_all_summaries(umo)
        merged = await self._merge_summaries(umo, old_total, summary)
        if not merged:
            logger.warning(f"[{PLUGIN_NAME}] 合并摘要失败，本轮不改动历史")
            return

        block_no = int(meta.get("blocks", 0)) + 1
        n_rounds = len(to_archive)
        self._append_archive(umo, block_no, n_rounds, summary)
        self._write_total_summary(umo, merged)

        # 只累加「历史上总共归档过多少轮」，纯展示用。
        # 不再拿它当游标 —— DB 轮数会被 AstrBot 回写裁剪，游标必然错位。
        total_archived = int(meta.get("archived_rounds", 0)) + n_rounds
        meta.update(
            {
                "archived_rounds": total_archived,
                "blocks": block_no,
                "last_archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_summary_chars": len(summary),
                "total_summary_chars": len(merged),
            }
        )
        self._save_meta(umo, meta)
        self._last_archive_at[umo] = now

        # 拼回请求：system 头 + 归档指针 + 近期原文。
        # 指针必须始终只有一条：旧的直接覆盖，不要把上一轮的指针叠进来。
        pointer_msgs = [
            {
                "role": "user",
                "content": (
                    f"[更早的 {total_archived} 轮对话已归档，以下是累计摘要]\n\n"
                    f"{self._build_pointer_text(umo)}"
                ),
            },
            {"role": "assistant", "content": ARCHIVE_POINTER_ACK},
        ]
        keep_msgs = [m for r in keep_recent for m in r]
        think_saved = 0
        if STRIP_THINK:
            keep_msgs, think_saved = _strip_think(keep_msgs)
        req.contexts = (system_head or []) + pointer_msgs + keep_msgs

        # 这里刻意只用估算、不采信 real：real 还是裁剪前的值，
        # 取 max 会让日志显示成「归档了但没降」，失去观察价值。
        after = _count_tokens(req.contexts) + _fixed_overhead(req, req.contexts)
        logger.info(
            f"[{PLUGIN_NAME}] 归档 {n_rounds} 轮 -> 上下文 {tokens} -> {after} token"
            f"（剥 think 省 {think_saved}），累计归档 {total_archived} 轮"
        )

    def _get_provider(self, umo: str | None):
        """拿当前会话的 provider，拿不到返回 None。"""
        try:
            provider = (
                self.context.get_using_provider(umo)
                if umo
                else self.context.get_using_provider()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 取 provider 失败: {exc}")
            return None
        if provider is None:
            logger.warning(f"[{PLUGIN_NAME}] 没有可用 provider，跳过归档")
        return provider

    async def _merge_summaries(
        self, umo: str | None, old: str, new: str
    ) -> str:
        """把旧累计摘要与新摘要合并成一个总摘要（A 方案）。

        ## 为什么要这一步
        旧实现把**全部**归档块拼进指针，私聊已累积到 3,861 token（占上下文 18%），
        而且每次归档都更长、从不淘汰。合并后指针长度有上限。

        ## 降级
        合并失败（provider 挂了 / 返回空）→ 直接拼接旧+新，
        **信息不丢**，只是指针长一点。
        归档是锦上添花，不该因为压缩失败而阻塞回复。
        """
        old = (old or "").strip()
        new = (new or "").strip()
        if not new:
            return old
        if not old:
            return new

        provider = self._get_provider(umo)
        if provider is None:
            return f"{old}\n\n{new}"

        # 占位符替换必须**一次性**完成。
        #
        # 为什么不能直接 replace：摘要正文里完全可能出现 "{old}" 这种字样
        # （比如用户在讲提示词工程），replace 会把内容里的占位符一起替换掉。
        # 为什么 split/join 也不够：替换 old 之后，正文里如果带 "{new}"，
        # 下一步替换 {new} 时会把它也换掉 —— **内容被二次替换**。
        #
        # 正确做法：先把模板里的占位符换成**不可能出现在正文里的哨兵**，
        # 再把哨兵换成正文。正文里的 "{old}" 长得像占位符，但它不是哨兵，不会被动。
        prompt = (
            MERGE_PROMPT.replace("{target}", _SENTINEL_TGT)
            .replace("{old}", _SENTINEL_OLD)
            .replace("{new}", _SENTINEL_NEW)
        )
        prompt = (
            prompt.replace(_SENTINEL_OLD, old)
            .replace(_SENTINEL_NEW, new)
            .replace(_SENTINEL_TGT, str(TOTAL_SUMMARY_TARGET_CHARS))
        )

        try:
            resp = await provider.text_chat(
                contexts=[{"role": "user", "content": prompt}],
                request_max_retries=1,
            )
            merged = (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 合并摘要失败（降级为拼接）: {exc}")
            return f"{old}\n\n{new}"

        if not merged:
            logger.warning(f"[{PLUGIN_NAME}] 合并摘要返回空（降级为拼接）")
            return f"{old}\n\n{new}"
        return merged

    async def _summarize(self, event: AstrMessageEvent, rounds: list[list[dict]]) -> str:
        umo = getattr(event, "unified_msg_origin", None)
        provider = self._get_provider(umo)
        if provider is None:
            return ""

        flat: list[dict] = []
        for rnd in rounds:
            for msg in rnd:
                text = _text_of(msg)
                if text.strip():
                    flat.append({"role": msg.get("role", "user"), "content": text})
        flat.append({"role": "user", "content": SUMMARY_PROMPT})

        try:
            resp = await provider.text_chat(contexts=flat, request_max_retries=1)
            return (resp.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 摘要生成失败: {exc}")
            return ""
