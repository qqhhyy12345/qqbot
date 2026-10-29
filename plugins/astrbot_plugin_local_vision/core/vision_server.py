"""按需拉起 llama-server，闲置自动卸掉。

设计目标：只在真正要看图的那几秒占显存，其余时间进程根本不存在。
这样主人一边打游戏一边跑机器人时，不会因为 2GB 显存被常驻占住而打架。

三道保险：
1. 启动前先探 /health —— 端口上已经有服务就直接复用，绝不重复拉起
2. 杀进程走 kill + wait，超时再补 taskkill /T，不留僵尸
3. 任何异常都返回 False，调用方降级成"这张图没认出来"，绝不影响回复

注意：本文件与 astrbot_plugin_qzone/core/vision_server.py 是同一份实现，
故意各自持有一份（跨插件 import 容易因加载顺序炸）。两边共用 1234 端口，
靠 /health 探测实现"谁先起来谁当宿主，另一个白嫖"，不会拉起两个 llama-server。
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger


class VisionServer:
    """管一个 llama-server 子进程的生命周期。"""

    def __init__(
        self,
        *,
        exe: str | Path,
        model: str | Path,
        mmproj: str | Path,
        port: int = 1234,
        device: str = "",          # 空 = 纯 CPU；"Vulkan0" 等则加 -dev
        gpu_layers: int = 0,
        ctx_size: int = 2048,
        mmproj_offload: bool = True,
        idle_timeout: float = 20.0,
        boot_timeout: float = 30.0,
        log_path: str | Path | None = None,
    ) -> None:
        self.exe = Path(exe)
        self.model = Path(model)
        self.mmproj = Path(mmproj)
        self.port = port
        self.device = device
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self.mmproj_offload = mmproj_offload
        self.idle_timeout = idle_timeout
        self.boot_timeout = boot_timeout
        self.log_path = Path(log_path) if log_path else None

        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._watchdog: asyncio.Task | None = None
        # 端口上是别人的服务（例如 QQ 空间插件拉起的），那就只借用、不杀
        self._external = False

    # ---------- 对外 ----------

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    def touch(self) -> None:
        """刷一下使用时间，告诉看门狗「还在用，别杀」。"""
        self._last_used = time.monotonic()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def files_ready(self) -> tuple[bool, str]:
        worker_mode = self.exe.name.lower().startswith("python")
        mmproj_label = "worker 脚本" if worker_mode else "视觉投影器"
        for p, label in ((self.exe, "推理引擎"), (self.model, "主模型"), (self.mmproj, mmproj_label)):
            if not p.exists():
                return False, f"{label}文件缺失: {p}"
        return True, ""

    async def ensure(self) -> bool:
        """确保服务可用。已就绪返回 True，拉不起来返回 False。"""
        ok, why = self.files_ready()
        if not ok:
            logger.warning(f"[vision] {why}")
            return False

        async with self._lock:
            if await self._healthy():
                if self._proc is None:
                    self._external = True
                self.touch()
                self._spawn_watchdog()
                return True

            if not await self._spawn():
                return False
            self.touch()
            return True

    async def stop(self) -> None:
        """杀掉自己拉起的进程。借用的外部服务不动。"""
        if self._external and self._proc is None:
            return
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return

        pid = proc.pid
        try:
            proc.kill()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=8)
        except ProcessLookupError:
            return
        except Exception as exc:
            logger.debug(f"[vision] kill 异常，改用 taskkill: {exc}")
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
        logger.info(f"[vision] llama-server 已退出（PID {pid}，闲置超过 {self.idle_timeout:.0f}s）")

    async def aclose(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        await self.stop()

    # ---------- 内部 ----------

    async def _healthy(self, timeout: float = 2.0) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                trust_env=False,
            ) as session:
                async with session.get(f"{self.base_url}/health") as resp:
                    if resp.status != 200:
                        return False
                    body = await resp.json(content_type=None)
                    return str(body.get("status", "")).lower() in (
                        "ok",
                        "ready",
                        "no slot available",
                    ) or resp.status == 200
        except Exception:
            return False

    async def _spawn(self) -> bool:
        # worker 模式：exe 是 python.exe，mmproj 参数位传的是 worker 脚本，
        # model 参数位传的是 OpenVINO IR 模型目录
        worker_mode = self.exe.name.lower().startswith("python")
        if worker_mode:
            args = [
                str(self.exe),
                str(self.mmproj),
                "--model", str(self.model),
                "--port", str(self.port),
                "--device", self.device or "GPU",
            ]
        else:
            args = [
                str(self.exe),
                "-m", str(self.model),
                "-mm", str(self.mmproj),
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "-c", str(self.ctx_size),
                "-np", "1",
                "-fa", "auto",
                "--no-webui",
            ]
            if self.device:
                args += ["-dev", self.device, "-ngl", str(self.gpu_layers)]
            if not self.mmproj_offload:
                args.append("--no-mmproj-offload")
            if self.log_path:
                with contextlib.suppress(Exception):
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                args += ["--log-file", str(self.log_path)]

        engine = "OpenVINO worker" if worker_mode else "llama-server"
        logger.info(f"[vision] 拉起 {engine}（device={self.device or '默认'}, 闲置 {self.idle_timeout:.0f}s 后卸）")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self.exe.parent),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.warning(f"[vision] 启动失败: {type(exc).__name__}: {exc}")
            self._proc = None
            return False

        self._external = False
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            if self._proc.returncode is not None:
                logger.warning(f"[vision] llama-server 启动即退出，code={self._proc.returncode}")
                self._proc = None
                return False
            if await self._healthy(timeout=2.0):
                logger.info("[vision] 本地视觉就绪")
                self._spawn_watchdog()
                return True
            await asyncio.sleep(0.4)

        logger.warning(f"[vision] 等待就绪超时（{self.boot_timeout:.0f}s）")
        await self.stop()
        return False

    def _spawn_watchdog(self) -> None:
        if self._watchdog and not self._watchdog.done():
            return
        self._watchdog = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(3.0)
                if self._proc is None:
                    return
                if time.monotonic() - self._last_used >= self.idle_timeout:
                    await self.stop()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"[vision] 看门狗异常退出: {exc}")
