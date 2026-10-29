"""本地语音转文字 —— 给其他插件用的 ASR 基座，顺手接管 QQ 语音消息。

## 能力

1. **Python 接口（主用途）**：其它插件直接
   `from data.plugins.astrbot_plugin_asr.core.asr import transcribe_file`
   然后 `await transcribe_file(path, max_minutes=5)`，返回
   `{"ok": True, "text": "...", "duration_s": 123}`。listen_bili 就这么用。
2. **QQ 语音消息自动转写**：挂 `on_llm_request`，把消息链里的 Record 组件
   就地换成 Plain 文字，并从请求里摘掉音频附件。主模型是纯文本模型，
   原始音频传上去既没用又可能报错；转写失败也换成一句说明，不留音频。
   **听完即删**：转写完立刻删掉 temp 里的语音文件（AstrBot 自己要到整轮结束才清），
   只删 temp 目录内的，别的插件自己的素材不碰。
3. **音频直链转写**：tool `asr_transcribe`，收 http(s) 直链，下载到 temp，
   转完即删。

## 边界

- 不做听歌识曲（没有曲库指纹）；纯音乐段 whisper 只会给省略号，会被过滤。
- 不做常驻服务：每次调用拉起 worker 子进程，干完即退，模型现加载（~2s）。
- 转写不是零成本：whisper-small int8 CPU 约 5~10 倍实时，长音频会占满 CPU，
  所以单次上限 15 分钟，同一时刻只跑一个（core/asr.py 全局锁）。
"""

from __future__ import annotations

import os
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .core.asr import MAX_MINUTES, delete_temp_audio, transcribe_file, transcribe_url

PLUGIN_NAME = "astrbot_plugin_asr"

VOICE_MAX_MINUTES = 5.0     # QQ 语音一般 ≤60s，留足余量给转发/合并消息
MAX_RECORDS = 2             # 一条消息最多转几条语音，防合并转发刷屏
MAX_TOOL_CHARS = 2000       # tool 回给模型的字数上限


class AsrPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ---------------- QQ 语音消息 ----------------

    @filter.on_llm_request()
    async def transcribe_voices(self, event: AstrMessageEvent,
                                req: ProviderRequest) -> None:
        try:
            await self._transcribe_records(event, req)
        except Exception as exc:  # noqa: BLE001
            # 转写失败绝不阻断对话
            logger.warning(f"[{PLUGIN_NAME}] 语音转写异常（已放行）: "
                           f"{type(exc).__name__}: {exc}")

    async def _transcribe_records(self, event: AstrMessageEvent,
                                  req: ProviderRequest) -> None:
        message = event.message_obj.message
        targets: list[tuple[list, int, Record]] = []
        for idx, comp in enumerate(message):
            if isinstance(comp, Record):
                targets.append((message, idx, comp))
        for comp in message:
            if isinstance(comp, Reply) and comp.chain:
                for idx, reply_comp in enumerate(comp.chain):
                    if isinstance(reply_comp, Record):
                        targets.append((comp.chain, idx, reply_comp))
        if not targets:
            return

        replaced_paths: set[str] = set()
        consumed: list[str] = []      # 转写/丢掉的语音文件，整条消息处理完再删
        appended: list[str] = []
        cache: dict[str, str] = {}
        for container, idx, comp in targets[:MAX_RECORDS]:
            path = await self._resolve_path(comp)
            if not path:
                continue
            key = self._norm(path)
            replacement = cache.get(key)
            if replacement is None:
                result = await transcribe_file(path, max_minutes=VOICE_MAX_MINUTES)
                if result.get("ok"):
                    text = (result.get("text") or "").strip()
                    replacement = text or "（语音里没有听清说话内容）"
                else:
                    logger.warning(f"[{PLUGIN_NAME}] QQ 语音转写失败: {result.get('error')}")
                    replacement = "（有一段语音，但没能转成文字）"
                cache[key] = replacement
            container[idx] = Plain(replacement)
            replaced_paths.add(key)
            appended.append(replacement)
            consumed.append(path)

        # 超额的语音不转写，但也绝不能把原始音频交给纯文本模型
        for container, idx, comp in targets[MAX_RECORDS:]:
            container[idx] = Plain("（语音太多，这条没有转写）")
            path = await self._resolve_path(comp)
            if path:
                replaced_paths.add(self._norm(path))
                consumed.append(path)

        # 听完即删：AstrBot 要等整轮结束才清 temp，这里转写完就先删掉
        for path in dict.fromkeys(consumed):
            event.untrack_temporary_local_file(str(Path(path).resolve()))
            delete_temp_audio(path)

        if not replaced_paths:
            return

        if appended:
            joined = "\n".join(appended)
            req.prompt = _append_text(req.prompt, joined)
            event.message_str = _append_text(event.message_str, joined)
            event.message_obj.message_str = _append_text(event.message_obj.message_str, joined)
            logger.info(f"[{PLUGIN_NAME}] 已转写 {len(cache)} 条 QQ 语音，"
                        f"共 {sum(len(t) for t in cache.values())} 字")
        self._drop_audio(req, replaced_paths)

    @staticmethod
    async def _resolve_path(comp: Record) -> str | None:
        for cand in (comp.path, comp.file):
            if not cand:
                continue
            text = str(cand)
            if text.startswith("file://"):
                text = text[7:]
            if Path(text).is_file():
                return text
        try:
            return await comp.convert_to_file_path()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _norm(path: str | Path) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(path)))
        except Exception:  # noqa: BLE001
            return str(path)

    def _drop_audio(self, req: ProviderRequest, replaced_paths: set[str]) -> None:
        """把已转写的音频从请求里摘掉：audio_urls + 附件说明文字。"""
        audio_urls = getattr(req, "audio_urls", None) or []
        req.audio_urls = [u for u in audio_urls if self._norm(u) not in replaced_paths]

        parts = list(getattr(req, "extra_user_content_parts", None) or [])
        if not parts:
            return
        kept = []
        for part in parts:
            text = str(getattr(part, "text", "") or "")
            norm_text = self._norm(text)
            if text.startswith("[Audio Attachment") and any(
                p in norm_text for p in replaced_paths
            ):
                continue
            kept.append(part)
        req.extra_user_content_parts = kept

    # ---------------- 音频链接转写 ----------------

    @filter.llm_tool(name="asr_transcribe")
    async def asr_transcribe(self, event: AstrMessageEvent, url: str,
                             minutes: float = 5.0) -> str:
        """把一段音频链接转成文字。

        适用场景：对方发来音频的 http(s) 直链，而你想知道里面说了什么。
        QQ 语音消息会自动转写成文字，不需要调用这个工具。
        听歌识曲做不到，只能听出说的话。
        Args:
            url(string): 音频直链（mp3/m4a/wav/ogg/flac 等）
            minutes(number): 最多转前几分钟，默认 5，最多 15
        """
        link = (url or "").strip()
        if not link.lower().startswith(("http://", "https://")):
            return "只支持 http(s) 音频直链。"
        try:
            minutes = max(1.0, min(float(minutes or 5.0), MAX_MINUTES))
        except (TypeError, ValueError):
            minutes = 5.0
        logger.info(f"[{PLUGIN_NAME}] 转写音频链接 {link[:80]}（前 {minutes:.0f} 分钟）")

        result = await transcribe_url(link, max_minutes=minutes)
        if not result.get("ok"):
            return f"这段音频转不了：{result.get('error', '未知错误')}"

        text = (result.get("text") or "").strip()
        if not text:
            return "这段音频里没有听出说话内容。"
        dur = int(result.get("duration_s") or 0)
        head = f"（{dur // 60}分{dur % 60}秒）" if dur else ""
        if len(text) > MAX_TOOL_CHARS:
            return head + text[:MAX_TOOL_CHARS] + "…（后面省略）"
        return head + text


def _append_text(existing: str | None, extra: str) -> str:
    base = (existing or "").strip()
    return f"{base}\n{extra}" if base else extra
