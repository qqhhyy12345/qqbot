"""B 站听视频 —— 下载音频，交给 ASR 插件转文字，再看旋律特征。

## 设计

- tool 触发（bili_listen），她想听才听：下载 + 推理都是重操作，
  不做自动钩子，防止有人刷一堆链接把 CPU 烧满。
- **转写复用 `astrbot_plugin_asr`**：这里只负责 B 站特有的部分（BV 解析、
  playurl 取音频流、cookie、下载），ASR 能力不再在本插件里重复实现。
- 旋律分析留在本插件（`core/melody_worker.py`，librosa 纯 DSP）：
  说话为主的视频（转写 >150 字）直接跳过，BPM/调性对着人声算出来的都是噪声。
- **听完即删**：下载的音频文件在 finally 里同步删除，成功失败都不留痕。
- 音频流地址走 playurl API（wbi 签名），自带 Referer 头；未配 cookie 也能
  用公开接口（限清晰度不影响音频轨）。
- 明确边界：听歌识曲（认出是哪首歌）做不到；ASR 对纯音乐段会过滤成空。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart

from .core.bili_client import MiniBili, UA

PLUGIN_NAME = "astrbot_plugin_listen_bili"

# ---- 可调参数 ----
PYTHON_EXE = Path(r"C:\Users\ximen\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
MELODY_SCRIPT = Path(__file__).resolve().parent / "core" / "melody_worker.py"
TEMP_DIR = Path(r"D:\qqbot\astrbot\data\temp")
BILI_COOKIE_CFG = Path(r"D:\qqbot\astrbot\data\config\astrbot_plugin_bilibili_config.json")
DEFAULT_MINUTES = 5.0
MAX_MINUTES = 15.0          # 单次最长听 15 分钟，防烧机
MELODY_TIMEOUT = 60.0       # 旋律 DSP 上限（解码 + librosa 几秒到几十秒）
DOWNLOAD_TIMEOUT = 60.0
MAX_AUDIO_MB = 60           # 音频下载上限（15 分钟 AAC ~15MB，放宽到 60）
TALK_TEXT_CHARS = 150       # 转写超过这个字数视为说话为主，跳过旋律分析

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")

_transcribe_file = None


def _transcribe():
    """拿到 ASR 插件的 transcribe_file；插件没加载/路径异常时按文件路径兜底。"""
    global _transcribe_file
    if _transcribe_file is not None:
        return _transcribe_file
    try:
        from data.plugins.astrbot_plugin_asr.core.asr import transcribe_file
    except Exception:  # noqa: BLE001
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "astrbot_plugin_asr" / "core" / "asr.py"
        spec = importlib.util.spec_from_file_location("astrbot_plugin_asr_client", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        transcribe_file = module.transcribe_file
    _transcribe_file = transcribe_file
    return transcribe_file


class ListenBiliPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_cookie() -> str:
        """复用 bilibili 浏览插件的 cookie（同账号，浏览器 F12 那份）。"""
        try:
            import io
            import json as _json
            cfg = _json.load(io.open(BILI_COOKIE_CFG, encoding="utf-8-sig"))
            return str(cfg.get("cookie", "")).strip()
        except Exception:  # noqa: BLE001 没有就裸奔（大概率被 412，error 由上层兜）
            return ""

    @staticmethod
    def parse_bvid(text: str) -> str | None:
        m = BV_RE.search(text or "")
        return m.group(1) if m else None

    # ---------------- B 站卡片识别 ----------------
    #
    # QQ 里从 B 站转发过来的卡片是 Json 组件（message_str 里只剩
    # "[ComponentType.Json]" 占位，BV 号被丢在组件 data 里）。
    # 这里挂 on_llm_request：把卡片里的 BV 号/标题抠出来，以文字注入，
    # 她就能直接"看到"卡片并决定要不要 bili_listen。

    _CARD_TITLE_RE = re.compile(r'"(?:desc|title|desc_long)":\s*"([^"]{2,80})"')
    _SHORT_RE = re.compile(r"https?://b23\.tv/[A-Za-z0-9]+")

    @staticmethod
    def _card_jsons(event: AstrMessageEvent) -> list[str]:
        """把消息链里所有 Json 组件的数据摊成字符串。"""
        out = []
        try:
            chain = list(event.get_messages() or [])
        except Exception:  # noqa: BLE001
            return out
        for comp in chain:
            data = getattr(comp, "data", None)
            if isinstance(data, dict):
                out.append(json.dumps(data, ensure_ascii=False))
            elif isinstance(data, str) and data:
                out.append(data)
        return out

    @classmethod
    def _card_title(cls, raw: str) -> str:
        m = cls._CARD_TITLE_RE.search(raw)
        if not m:
            return ""
        return m.group(1).replace("\\u002F", "/").replace("\\/", "/").strip()

    async def _resolve_short(self, url: str) -> str:
        """b23.tv 短链 → 302 跳转后的真实地址（含 BV 号）。失败返回空。"""
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.head(
                    url, allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": UA},
                ) as resp:
                    return str(resp.url)
        except Exception:  # noqa: BLE001
            return ""

    async def _reveal_cards(self, event: AstrMessageEvent) -> list[str]:
        """从卡片 JSON 里识别 B 站视频，返回要注入的描述。"""
        found: list[str] = []
        seen: set[str] = set()
        for raw in self._card_jsons(event):
            if "b23.tv" not in raw and "bilibili" not in raw.lower():
                continue
            title = self._card_title(raw)
            bvid = self.parse_bvid(raw)
            if not bvid:
                for url in self._SHORT_RE.findall(raw):
                    final = await self._resolve_short(url)
                    bvid = self.parse_bvid(final)
                    if bvid:
                        break
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)
            found.append(
                f"[B站卡片：{title or '标题未知'}，BV号 {bvid}。对方给你发了这个视频，"
                "想了解内容可以用 bili_listen 听。]"
            )
        return found

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def decode_cards(self, event: AstrMessageEvent) -> None:
        """卡片前置解码（管线早期，先于 LLM 门控）。

        B 站卡片常以 Json 组件到达且 message_str 为空——空消息可能不触发 LLM，
        她就永远看不到卡片。这里把解码结果直接写回 message_str：
        一让她"看到"卡片，二让消息不再为空、门控照常放行。
        """
        try:
            chain = list(event.get_messages() or [])
            if not chain:
                return
            types = {type(c).__name__ for c in chain}
            if "Json" in types:
                logger.info(f"[{PLUGIN_NAME}] 检测到 Json 组件，尝试解码卡片…")
            if "Json" not in types:
                return
            cards = await self._reveal_cards(event)
            if not cards:
                return
            note = " ".join(cards)
            event.message_str = (event.message_str + " " + note).strip()
            with contextlib.suppress(Exception):
                event.message_obj.message_str = event.message_str
            logger.info(
                f"[{PLUGIN_NAME}] 卡片解码：已回写 message_str（{len(cards)} 张）"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 卡片前置解码失败（已放行）: {exc}")

    @filter.on_llm_request()
    async def reveal_cards(self, event: AstrMessageEvent, req) -> None:
        try:
            if req is None or "[B站卡片" in (event.message_str or ""):
                return  # decode_cards 已回写过了，别重复注入
            cards = await self._reveal_cards(event)
            if not cards:
                return
            parts = list(req.extra_user_content_parts or [])
            parts.extend(TextPart(text=c) for c in cards)
            req.extra_user_content_parts = parts
            logger.info(f"[{PLUGIN_NAME}] 识别到 B 站卡片 {len(cards)} 张，已注入")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 卡片识别失败（已放行）: {exc}")

    async def _download_audio(self, session: aiohttp.ClientSession, url: str) -> Path:
        dest = TEMP_DIR / f"bili_audio_{uuid.uuid4().hex[:8]}.m4s"
        total = 0
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
                                   headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"音频流 HTTP {resp.status}")
                with dest.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
                        total += len(chunk)
                        if total > MAX_AUDIO_MB * 1024 * 1024:
                            raise RuntimeError("音频超过大小上限，放弃")
            if total < 1024:
                raise RuntimeError("音频流为空")
            return dest
        except Exception:
            dest.unlink(missing_ok=True)  # 下载失败也不留残骸
            raise

    async def _analyze_melody(self, audio_path: Path, minutes: float) -> dict:
        proc = await asyncio.create_subprocess_exec(
            str(PYTHON_EXE), str(MELODY_SCRIPT),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            job = json.dumps({"audio_path": str(audio_path), "max_minutes": minutes})
            out, _ = await asyncio.wait_for(proc.communicate(job.encode()),
                                            timeout=MELODY_TIMEOUT)
            payload = json.loads(out.decode("utf-8", "ignore").strip().splitlines()[-1])
            return (payload.get("music") or {}) if payload.get("ok") else {}
        except Exception as exc:  # noqa: BLE001 旋律分析挂了不影响转写
            logger.debug(f"[{PLUGIN_NAME}] 旋律分析失败: {type(exc).__name__}: {exc}")
            return {}
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass

    async def _fmt_result(self, result: dict, title: str, minutes: float) -> str:
        if not result.get("ok"):
            return f"听不了这个视频：{result.get('error', '未知错误')}"
        text = (result.get("text") or "").strip()
        music = result.get("music") or {}
        dur = int(result.get("duration_s", 0))
        lines = [f"《{title}》"]
        if dur:
            cap = f"（听了前 {minutes:.0f} 分钟）" if dur >= minutes * 60 - 5 else ""
            lines.append(f"语音内容（{dur//60}分{dur%60}秒{cap}）：{text[:1200] or '（没有说话，纯音乐/环境音）'}")
        else:
            lines.append(f"语音内容：{text[:1200] or '（没有说话）'}")
        if music and not music.get("error"):
            bits = []
            if music.get("bpm"):
                bits.append(f"约 {music['bpm']:.0f} BPM")
            if music.get("key"):
                bits.append(f"{music['key']}")
            if music.get("range"):
                bits.append(f"音域 {music['range']}")
            if bits:
                lines.append("旋律特征：" + "，".join(bits) +
                             (f"；走向 {music['contour']}" if music.get("contour") else ""))
            if music.get("has_melody") is False:
                lines.append("（这段听起来没有明显旋律，像说话或环境音）")
        return "\n".join(lines)

    async def _listen(self, bvid: str, minutes: float) -> str:
        client = MiniBili(cookie=self._load_cookie())
        audio_path: Path | None = None
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                await client.init_wbi(session)
                url, title, dur = await client.get_audio_url(session, bvid)
                take = min(minutes, MAX_MINUTES, max(1.0, dur / 60.0 + 0.5))
                audio_path = await self._download_audio(session, url)

            result = await _transcribe()(audio_path, max_minutes=take)
            text = (result.get("text") or "") if result.get("ok") else ""
            music: dict = {}
            if result.get("ok") and len(text) <= TALK_TEXT_CHARS:
                music = await self._analyze_melody(audio_path, take)
            merged = {
                "ok": result.get("ok"),
                "error": result.get("error"),
                "text": text,
                "duration_s": result.get("duration_s", 0),
                "music": music,
            }
            return await self._fmt_result(merged, title, take)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 听视频失败: {type(exc).__name__}: {exc}")
            return f"听不了这个视频：{type(exc).__name__}: {str(exc)[:80]}"
        finally:
            # 听完即删：无论成败，音频文件生命周期到此为止
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)

    # ---------------- tool ----------------

    from astrbot.api.event import filter as _filter

    @_filter.llm_tool()
    async def bili_listen(self, event, video: str, minutes: float = 5.0):
        """听一个B站视频：把里面的说话内容转成文字，并分析音乐的旋律特征（BPM/调性/音域/走向）。

        适用场景：对方发来 B 站视频链接、或你在 B 站搜到想了解内容的视频。
        听歌识曲（认出这是哪首歌）做不到，只能描述旋律特征。
        Args:
            video(string): B站视频的 BV 号或链接（支持 b23.tv 短链里的 BV 号）
            minutes(number): 听前多少分钟，默认 5，最多 15
        """
        bvid = self.parse_bvid(str(video))
        if not bvid:
            return "没认出 BV 号，把视频链接或 BV 号发给我（形如 BV1xx…）"
        minutes = max(1.0, min(float(minutes or DEFAULT_MINUTES), MAX_MINUTES))
        logger.info(f"[{PLUGIN_NAME}] 听 {bvid} 前 {minutes:.0f} 分钟")
        return await self._listen(bvid, minutes)
