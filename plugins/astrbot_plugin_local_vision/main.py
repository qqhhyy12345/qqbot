"""聊天图片本地转述 —— 图片不进云端，只把一句中文描述发过去。

## 为什么需要它

AstrBot 的 provider 配置里 `modalities: []` 会被判定成「支持图片」，
于是聊天里的图会以 base64 原样塞进请求，一张约 1,500 token，还会在
后续每轮里跟着回传。QQ 空间那条链路早就换成本地视觉了，聊天这边一直没换。

## 做法

挂 `on_llm_request`，在请求真正发出前把图片换掉：

- 本轮新图（`req.image_urls`）→ 本地 Qwen2-VL-2B 转述成一句中文 → 清空 image_urls
- 历史里的图（`contexts` 里 `type=="image_url"` 的 part）→ 同样换成文本
- 引用消息里的图（`extra_user_content_parts` 里的 ImageURLPart）→ 同上

## 安全边界

- 任何一张图认不出来 → **保留原图**，退回云端视觉，绝不让她"看不见图"
- 服务懒启动：只在看图那几秒存在，闲置 20 秒自动退出，不常驻显存
- 转述结果按图片内容哈希缓存，同一张图只算一次
- 转述成功后**不立刻删**原图（本轮别的环节还要用）；等下次来图时顺手清旧的：
  只删 astrbot/data/temp 下 `media_image*` 且生成超过 2 分钟的文件，
  同步执行 + 30 秒节流，不依赖任何后台任务
- 与 QQ 空间插件共用 1234 端口，靠 /health 探测复用，不会拉起两个 llama-server
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart

from .core.vision_server import VisionServer

PLUGIN_NAME = "astrbot_plugin_local_vision"


class LocalVisionPlugin(Star):
    # ---- 可调参数 ----
    VISION_PORT = 1235             # 独立端口（qzone 的 llama-server 用 1234，互不干扰）
    VISION_DEVICE = "GPU"          # OpenVINO 用 Iris Xe 核显
    VISION_IDLE_TIMEOUT = 60.0     # 闲置多少秒卸进程（worker 冷启动 ~7s，比 llama-server 久，放宽保缓存）
    VISION_MODEL = "qwen2-vl-2b-instruct"
    VISION_MAX_EDGE = 960           # 送进模型前缩到长边 960，推理更快
    VISION_JPEG_QUALITY = 80
    VISION_TIMEOUT = 60.0           # 单次推理上限（OpenVINO 960px 实测 ~10s + 冷启动 ~7s）
    VISION_MAX_IMAGES = 3           # 一条消息最多转述几张，防有人一次发九宫格
    CACHE_MAX = 300

    # 转述成功后不立刻删：本轮别的环节可能还要用这个文件。
    # 改成「下次来图时顺手清旧的」——同步执行，不排后台任务，
    # 因此不存在任务被 GC 或标记卡死的问题。
    DELETE_LOCAL_COPY = True
    SWEEP_MIN_AGE = 120.0       # 只删生成超过 2 分钟的文件，绝不碰正在处理的那张
    SWEEP_MIN_INTERVAL = 30.0   # 两次清扫的最小间隔，防群聊刷图时反复扫目录

    # OpenVINO 三件套：python 解释器 / worker 脚本 / IR 模型目录
    VISION_SERVER_EXE = Path(
        r"C:\Users\ximen\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    )
    VISION_MODEL_PATH = Path(
        r"D:\qqbot\models\qwen2vl-2b-ov"
    )
    VISION_MMPROJ_PATH = Path(
        r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_local_vision\core\ov_worker.py"
    )
    VISION_PROMPT = (
        "用一句中文描述这张图片的内容。"
        "如果是表情包或截图，说明它是什么、上面写了什么字。"
        "如果图里有文字，把文字如实念出来。"
        "只输出描述本身，不要寒暄，不要说明你在做什么。"
    )

    def __init__(self, context: Context):
        super().__init__(context)
        log_path = Path(__file__).resolve().parents[2] / "temp" / "llama-server-chat.log"
        self._vision = VisionServer(
            exe=self.VISION_SERVER_EXE,
            model=self.VISION_MODEL_PATH,
            mmproj=self.VISION_MMPROJ_PATH,
            port=self.VISION_PORT,
            device=self.VISION_DEVICE,
            idle_timeout=self.VISION_IDLE_TIMEOUT,
            log_path=log_path,
        )
        self._cache: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._last_sweep_at = 0.0  # 上次清扫的时间戳，用于节流

    # ---------------- 钩子 ----------------

    @filter.on_llm_request()
    async def swap_images(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        try:
            await self._swap(req)
        except Exception as exc:  # noqa: BLE001
            # 出任何岔子都退回原图，绝不让转述影响回复
            logger.warning(f"[{PLUGIN_NAME}] 图片转述异常，保留原图: {type(exc).__name__}: {exc}")

    async def _swap(self, req: ProviderRequest) -> None:
        contexts = req.contexts
        if isinstance(contexts, str):
            try:
                parsed = json.loads(contexts)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                req.contexts = parsed
                contexts = parsed
            else:
                contexts = None

        # 1) 历史里的图
        if isinstance(contexts, list):
            for msg in contexts:
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for idx, part in enumerate(content):
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "image_url":
                        continue
                    src = str((part.get("image_url") or {}).get("url") or "")
                    desc = await self._describe(src)
                    if desc:
                        content[idx] = {"type": "text", "text": f"[图片：{desc}]"}

        # 2) 引用消息里的图
        extra = list(req.extra_user_content_parts or [])
        if extra:
            changed = False
            for idx, part in enumerate(extra):
                if getattr(part, "type", "") != "image_url":
                    continue
                url = str(getattr(getattr(part, "image_url", None), "url", "") or "")
                desc = await self._describe(url)
                if desc:
                    extra[idx] = TextPart(text=f"[引用图片：{desc}]")
                    changed = True
            if changed:
                req.extra_user_content_parts = extra

        # 3) 本轮新图
        urls = [u for u in dict.fromkeys(req.image_urls or []) if u]
        if not urls:
            return

        # 来图了：先把 temp 里的旧图清一遍。
        # 同步执行，不排后台任务 —— 这一步返回时旧图必然已经删掉，
        # 不存在任务被 GC、标记卡死之类的时序问题。
        # 本轮这张的 mtime 是刚写下的（0~2 秒），够不着 SWEEP_MIN_AGE，不会被误删。
        await self._sweep_temp()

        budget = urls[: self.VISION_MAX_IMAGES]
        leftover = urls[self.VISION_MAX_IMAGES :]
        descs: list[str] = []
        keep: list[str] = []
        for src in budget:
            desc = await self._describe(src)
            if desc:
                descs.append(desc)
            else:
                keep.append(src)  # 认不出来就留着原图，让云端看

        if not descs:
            return

        parts = list(req.extra_user_content_parts or [])
        for i, desc in enumerate(descs, 1):
            label = f"[图片{i}：" if len(descs) > 1 else "[图片："
            parts.append(TextPart(text=f"{label}{desc}]"))
        req.extra_user_content_parts = parts
        req.image_urls = keep + leftover
        logger.info(
            f"[{PLUGIN_NAME}] 本地转述 {len(descs)} 张（保留原图 {len(req.image_urls)} 张）"
        )

    # ---------------- 转述 ----------------

    async def _describe(self, src: str) -> str | None:
        """图片 → 一句中文描述。拿不到就返回 None（调用方保留原图）。

        两级缓存：先按来源字符串（省一次读盘/下载），再按图片内容哈希
        （同一张表情包从不同临时路径发来，也只会真的算一次）。
        """
        if not src:
            return None
        src_key = hashlib.sha1(src.encode("utf-8", "ignore")).hexdigest()
        if src_key in self._cache:
            return self._cache[src_key]

        async with self._lock:
            if src_key in self._cache:
                return self._cache[src_key]

            data, mime = await self._load_image(src)
            if not data:
                return None

            data_key = hashlib.sha1(data).hexdigest()
            if data_key in self._cache:
                self._cache[src_key] = self._cache[data_key]
                return self._cache[data_key]

            data, mime = self._shrink(data, mime)
            text = await self._ask(data, mime)
            if not text:
                return None
            if len(self._cache) > self.CACHE_MAX * 2:
                self._cache.clear()
            self._cache[data_key] = text
            self._cache[src_key] = text
            return text

    async def _load_image(self, src: str) -> tuple[bytes | None, str]:
        if src.startswith("data:"):
            try:
                head, b64 = src.split(",", 1)
                mime = head[5:].split(";")[0] or "image/jpeg"
                return base64.b64decode(b64), mime
            except Exception as exc:
                logger.debug(f"[{PLUGIN_NAME}] data URI 解码失败: {exc}")
                return None, "image/jpeg"

        if src.startswith(("http://", "https://")):
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(
                    timeout=timeout, connector=connector, trust_env=False
                ) as session:
                    async with session.get(src) as resp:
                        if resp.status != 200:
                            return None, "image/jpeg"
                        return await resp.read(), resp.headers.get("Content-Type", "image/jpeg")
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 图片下载失败（{type(exc).__name__}）: {src[:80]}")
                return None, "image/jpeg"

        path = Path(src[7:] if src.startswith("file://") else src)
        try:
            if path.exists():
                return path.read_bytes(), "image/jpeg"
        except Exception as exc:
            logger.debug(f"[{PLUGIN_NAME}] 读本地图失败: {exc}")
        return None, "image/jpeg"

    def _shrink(self, data: bytes, mime: str) -> tuple[bytes, str]:
        """缩到长边 960 并重编码 JPEG，省本地推理时间。失败就原样返回。"""
        try:
            from PIL import Image

            im = Image.open(io.BytesIO(data))
            if getattr(im, "is_animated", False):
                return data, mime if mime.startswith("image/") else "image/gif"
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            if max(im.size) > self.VISION_MAX_EDGE:
                im.thumbnail((self.VISION_MAX_EDGE, self.VISION_MAX_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=self.VISION_JPEG_QUALITY, optimize=True)
            out = buf.getvalue()
            if out and len(out) < len(data):
                return out, "image/jpeg"
        except Exception as exc:
            logger.debug(f"[{PLUGIN_NAME}] 图片压缩失败，用原图: {exc}")
        return data, "image/jpeg"

    async def _ask(self, data: bytes, mime: str) -> str | None:
        if not await self._vision.ensure():
            return None
        self._vision.touch()

        data_uri = f"data:{mime};base64," + base64.b64encode(data).decode()
        payload: dict[str, Any] = {
            "model": self.VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "max_tokens": 160,
            "temperature": 0.2,
            "stream": False,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.VISION_TIMEOUT),
                trust_env=False,
            ) as session:
                async with session.post(self._vision.chat_url, json=payload) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:120]
                        logger.warning(f"[{PLUGIN_NAME}] 本地视觉 HTTP {resp.status}: {body}")
                        return None
                    result = await resp.json()
        except asyncio.TimeoutError:
            logger.warning(f"[{PLUGIN_NAME}] 本地视觉超时（{self.VISION_TIMEOUT:.0f}s）")
            return None
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 本地视觉调用失败（{type(exc).__name__}）: {exc}")
            return None
        finally:
            self._vision.touch()

        try:
            text = result["choices"][0]["message"]["content"] or ""
        except Exception:
            return None
        text = str(text).strip()
        return text or None

    # ---------------- 清理本地原图 ----------------

    def _temp_root(self) -> Path:
        """AstrBot 的临时媒体目录：astrbot/data/temp"""
        return (Path(__file__).resolve().parents[2] / "temp").resolve()

    async def _sweep_temp(self, force: bool = False) -> int:
        """删掉 temp 里过期的 media_image 文件，返回删除数量。

        策略：**下次来图时顺手清旧的**。由 `_swap` 在本轮有图时同步调用，
        不做后台延时任务 —— 早先那版用 `asyncio.create_task` 排延时任务，
        既有被 GC 的风险，又要维护 `_sweep_pending` 标记（取消时还可能卡死）。

        只删 `astrbot/data/temp` 下、`media_image` 前缀、生成超过
        SWEEP_MIN_AGE 秒的文件。本轮刚收到的那张 mtime 是 0~2 秒，
        够不着门槛，绝不会被误删。

        `force=True` 跳过节流（测试用）。
        任何异常都吞掉并打 debug 日志：清扫失败绝不能影响她的回复。
        """
        if not self.DELETE_LOCAL_COPY:
            return 0
        now = time.time()
        if not force and now - self._last_sweep_at < self.SWEEP_MIN_INTERVAL:
            return 0  # 30 秒内已经扫过，群聊刷图时不必反复扫目录
        self._last_sweep_at = now
        try:
            root = self._temp_root()
            if not root.exists():
                return 0
            cutoff = now - self.SWEEP_MIN_AGE
            removed = 0
            for path in root.glob("media_image*"):
                try:
                    if not path.is_file() or path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                    removed += 1
                except Exception:
                    continue  # 单个删不掉（比如被占用）不影响其他
            if removed:
                logger.info(f"[{PLUGIN_NAME}] 已清理 {removed} 个旧图")
            return removed
        except Exception as exc:
            logger.debug(f"[{PLUGIN_NAME}] 清扫失败（忽略，不影响对话）: {exc}")
            return 0

    async def terminate(self) -> None:
        try:
            await self._vision.aclose()
        except Exception:
            pass
