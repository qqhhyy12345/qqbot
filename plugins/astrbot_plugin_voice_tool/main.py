"""语音工具 —— 把"发不发语音"的决定权交给模型。

背景：AstrBot 原生 TTS 是按 `random.random() <= trigger_probability` 掷骰子的，
模型完全不参与。本插件换成工具调用：模型想出声时自己调 send_voice。

两个行为约定：

- **只回状态码**：`已发送=1` 表示真发出去了，模型拿到就不必再确认、不必重发；
  `未发送=0（原因）` 表示没发成，模型直接改用文字。
- **发完即删**：合成出来的 wav 落在 AstrBot temp 里，发完立刻删（引擎自己不清）。

只做一件事，不接管任何钩子，关掉即失效。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star
from astrbot.core.utils.media_utils import get_astrbot_temp_path

try:  # AstrBot 4.x 主流路径
    from astrbot.core.message.message_event_result import MessageChain
except Exception:  # pragma: no cover - 版本回退
    from astrbot.api.all import MessageChain

MAX_CHARS = 300  # 太长合成慢、听着累

TEMP_DIR = Path(get_astrbot_temp_path())


def _delete_temp_audio(path: str | Path) -> None:
    """只删 AstrBot temp 目录里的合成音频；目录外的文件一律不碰。"""
    target = Path(str(path))
    try:
        target.resolve().relative_to(TEMP_DIR.resolve())
    except (OSError, ValueError):
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


class VoiceToolPlugin(Star):
    """想说话的时候用 send_voice，默认还是打字。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)

    async def _resolve_tts_provider(self, umo: str):
        """优先取会话选中的 TTS，取不到就直接拿管理器里已解析的那个实例。

        `provider_tts_settings.enable=false`（关掉随机语音）会让
        get_using_tts_provider_async() 直接返回 None，而工具语音不该受随机语音开关影响。
        """
        try:
            provider = await self.context.get_using_tts_provider_async(umo)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[voice_tool] 获取 TTS 服务失败: {exc}")
            provider = None
        if provider is None:
            provider = getattr(
                self.context.provider_manager, "curr_tts_provider_inst", None
            )
        if provider is None:
            providers = self.context.get_all_tts_providers()
            provider = providers[0] if providers else None
        return provider

    @filter.llm_tool(name="send_voice")
    async def send_voice(self, event: AstrMessageEvent, text: str) -> str:
        """把一段话用语音念出来发给对方。只有在你真想"出声"的时候才用——比如语气比文字更要紧、想哄人、或者想逗对方一下。默认还是打字，别把每条消息都念出来。

        返回 `已发送=1` 就是已经发出去了，不要再确认、不要再查、也别把这句话再重复一遍；
        返回 `未发送=0（原因）` 说明没发成，直接用文字说。

        Args:
            text(string): 要念出来的话，一到三句，别太长
        """
        text = (text or "").strip()
        if not text:
            return "未发送=0（没有内容）"
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]

        provider = await self._resolve_tts_provider(event.unified_msg_origin)
        if provider is None:
            return "未发送=0（没有可用的语音服务），改用文字。"

        try:
            audio_path = await provider.get_audio(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[voice_tool] 语音合成失败: {exc}")
            return "未发送=0（语音合成失败），改用文字。"
        if not audio_path:
            return "未发送=0（语音没生成出来），改用文字。"

        try:
            await event.send(
                MessageChain([Record(file=audio_path, url=audio_path, text=text)])
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[voice_tool] 语音发送失败: {exc}")
            return "未发送=0（语音发送失败），改用文字。"
        finally:
            # 发完即删（失败也删），否则合成音频会一直堆在 temp 里
            _delete_temp_audio(audio_path)

        logger.info(f"[voice_tool] 已发送语音 {len(text)} 字")
        return "已发送=1"
