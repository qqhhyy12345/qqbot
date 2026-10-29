"""唱歌工具 —— 让模型把歌词和简谱交给 DiffSinger 唱出来。

渲染跑在独立子进程里，用的是 `_uv_tools\\singing\\.venv` 的解释器：那边才有
torch / onnxruntime，不往 AstrBot 的环境里塞东西，渲染崩了也拖不死机器人。

两个行为约定，和 voice_tool 保持一致：

- **只回状态码**：`已发送=1` 表示真发出去了，模型拿到就不必再确认、不必重发；
  `未发送=0（原因）` 表示没唱成，模型直接改用文字。
- **发完即删**：合成出来的 mp3 落在 AstrBot temp 里，发完立刻删。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
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

SINGING_PYTHON = Path(r"D:\qqbot\_uv_tools\singing\.venv\Scripts\python.exe")
VOICE_BANK = Path(
    r"D:\qqbot\_uv_tools\singing\voicebank\Qixuan_v2.7.0_DiffSinger_OpenUtau"
)
WORKER = Path(__file__).with_name("sing_render.py")

TEMP_ROOT = Path(get_astrbot_temp_path()) / "singing"

MAX_LYRICS = 40  # 再长就要唱到语音消息上限外面去了
TIMEOUT = 300  # 冷启动 2s + 渲染约 0.8× 时长，留足余量

# 渲染目录名就是我们自己发的 uuid 前缀，只认这个形状，别的文件一概不碰
_WORK_NAME = re.compile(r"^[0-9a-f]{12}$")


def _sweep_temp() -> None:
    """清掉上次进程被硬杀时没来得及删的渲染目录。"""
    if not TEMP_ROOT.exists():
        return
    swept = 0
    for entry in TEMP_ROOT.iterdir():
        if entry.is_dir() and _WORK_NAME.match(entry.name):
            shutil.rmtree(entry, ignore_errors=True)
            swept += 1
    if swept:
        logger.info(f"[singing] 清掉 {swept} 个上次残留的渲染目录")


class SingingPlugin(Star):
    """想唱的时候用 sing，平时还是打字。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self._lock = asyncio.Lock()  # 渲染吃满 CPU，一次只跑一个
        try:
            _sweep_temp()
        except Exception as exc:  # noqa: BLE001 - 清理失败不能拖累插件加载
            logger.warning(f"[singing] 清理残留失败: {exc}")

    async def _render(self, work: Path, lyrics: str, melody: str) -> dict:
        """起一个子进程渲染，拿回 RESULT 行。"""
        work.mkdir(parents=True, exist_ok=True)
        payload = {
            "lyrics": lyrics,
            "melody": melody,
            "tempo": 120,
            "base": "C4",
            "voice_bank": str(VOICE_BANK),
            "out_dir": str(work),
        }

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        proc = await asyncio.create_subprocess_exec(
            str(SINGING_PYTHON),
            str(WORKER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        stdout = out.decode("utf-8", "replace")
        for line in reversed(stdout.splitlines()):
            if line.startswith("RESULT:"):
                return json.loads(line[len("RESULT:") :])

        stderr = err.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(stderr[-1] if stderr else "渲染进程没有返回结果")

    @filter.llm_tool(name="sing")
    async def sing(
        self, event: AstrMessageEvent, lyrics: str, melody: str = ""
    ) -> str:
        """用 DiffSinger 把人声唱出来，以语音消息发送。只在对方明确要你"唱"、并且给得出歌词和简谱时用。

        返回 `已发送=1` 就是已经发出去了，不要再确认、不要再查、也别重发；
        返回 `未发送=0（原因）` 说明没唱成，直接用文字说。

        Args:
            lyrics(string): 要唱的歌词，只写汉字、不要标点，一个字唱一个音
            melody(string): 简谱，1-7 是 do-si，空格分隔，一个字一个音；`-` 表示前一个音延长一拍；`#`/`b` 升降半音，`'`/`,` 升降八度；1 = C4。对方没给简谱就别自己编，问清楚再唱
        """
        lyrics = (lyrics or "").strip()
        melody = (melody or "").strip()

        if not lyrics:
            return "未发送=0（没有歌词）"
        if not melody:
            return "未发送=0（没有简谱），改用文字并问清楚 1-7 的简谱旋律。"
        if len(lyrics) > MAX_LYRICS:
            return f"未发送=0（歌词超过 {MAX_LYRICS} 字），分段唱。"
        if not (WORKER.exists() and SINGING_PYTHON.exists() and VOICE_BANK.exists()):
            logger.error("[singing] 渲染环境缺失，检查 _uv_tools/singing")
            return "未发送=0（唱歌环境缺失），改用文字。"

        work = TEMP_ROOT / uuid.uuid4().hex[:12]
        try:
            async with self._lock:
                result = await self._render(work, lyrics, melody)

            if not result.get("ok"):
                return f"未发送=0（{result.get('error', '渲染失败')}）"

            mp3 = Path(result["mp3"])
            if not mp3.exists():
                return "未发送=0（没有生成音频文件）"

            await event.send(MessageChain([Record(file=str(mp3), text=lyrics)]))
            logger.info(
                f"[singing] 已发送 {result.get('duration')}s / "
                f"{result.get('syllables')} 字 / 渲染 {result.get('render_sec')}s"
            )
            return "已发送=1"

        except asyncio.TimeoutError:
            logger.error("[singing] 渲染超时")
            return "未发送=0（渲染超时），改用文字。"
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[singing] 唱歌失败: {exc}")
            return f"未发送=0（{exc}），改用文字。"
        finally:
            shutil.rmtree(work, ignore_errors=True)
