"""本地语音转文字接口 —— 插件内部与其它插件共用的唯一入口。

公开 API（其它插件直接 import 即可，不需要知道 worker 细节）:

    from data.plugins.astrbot_plugin_asr.core.asr import transcribe_file, transcribe_url

    result = await transcribe_file(r"D:\\x\\a.m4a", max_minutes=5)
    # {"ok": True,  "text": "...", "duration_s": 123}
    # {"ok": False, "error": "..."}

    result = await transcribe_url("https://example.com/a.mp3", max_minutes=5)
    # 同上；下载的临时文件在返回前一定删掉

## 为什么是子进程

faster-whisper / av / opencc 只装在 WorkBuddy 的 Python 里，AstrBot 主环境没有。
所以真正干活的是同目录的 asr_worker.py，用那个解释器拉起，干完即退。

## 两条约定

- **不抛异常**：所有失败都以 `{"ok": False, "error": ...}` 返回，调用方自己兜。
- **全局串行**：同一时刻只跑一个 worker。int8 已经吃满 14 线程，并发只会互相拖慢，
  而且每进程都要重新加载模型（~2s），并行没有收益。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import aiohttp

WORKER_PY = Path(r"C:\Users\ximen\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
WORKER_SCRIPT = Path(__file__).resolve().parent / "asr_worker.py"
MODEL_DIR = Path(r"D:\qqbot\models\faster-whisper-small")
TEMP_DIR = Path(r"D:\qqbot\astrbot\data\temp")

DEFAULT_MINUTES = 5.0
MAX_MINUTES = 15.0          # 单次转写上限，防一次拉起半小时音频把机器拖死
JOB_TIMEOUT = 300.0         # worker 整体超时
DOWNLOAD_TIMEOUT = 60.0
MAX_AUDIO_MB = 60
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_lock = asyncio.Lock()


def _clamp_minutes(minutes) -> float:
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        value = DEFAULT_MINUTES
    return max(0.1, min(value, MAX_MINUTES))


def delete_temp_audio(path: str | Path) -> bool:
    """删掉 AstrBot temp 目录下的音频文件。

    只碰 temp 目录里的东西：调用方传进来的文件（别的插件自己的素材）绝不删，
    目录外的路径直接返回 False。删不掉（被占用等）也只返回 False，不抛异常。
    """
    target = Path(str(path))
    try:
        target.resolve().relative_to(TEMP_DIR.resolve())
    except (OSError, ValueError):
        return False
    try:
        target.unlink(missing_ok=True)
        return True
    except OSError:
        return False


async def transcribe_file(audio_path: str | Path,
                          max_minutes: float = DEFAULT_MINUTES) -> dict:
    """本地音频文件 -> 文字。找不到文件/超时/worker 报错都返回 ok=False。"""
    path = Path(str(audio_path))
    if not path.is_file():
        return {"ok": False, "error": f"音频文件不存在: {path.name}"}
    if not WORKER_PY.is_file():
        return {"ok": False, "error": f"ASR 解释器不存在: {WORKER_PY}"}
    if not MODEL_DIR.is_dir():
        return {"ok": False, "error": f"ASR 模型目录不存在: {MODEL_DIR}"}

    minutes = _clamp_minutes(max_minutes)
    async with _lock:
        proc = await asyncio.create_subprocess_exec(
            str(WORKER_PY), str(WORKER_SCRIPT), str(MODEL_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            job = json.dumps({"audio_path": str(path), "max_minutes": minutes})
            out, _ = await asyncio.wait_for(proc.communicate(job.encode()),
                                            timeout=JOB_TIMEOUT)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"转写超时（{JOB_TIMEOUT:.0f}s）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass

    lines = out.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return {"ok": False, "error": "worker 没有输出"}
    try:
        return json.loads(lines[-1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"worker 输出无法解析: {lines[-1][:120]}"}


async def transcribe_url(url: str, max_minutes: float = DEFAULT_MINUTES) -> dict:
    """音频直链 -> 文字。下载到 temp，转完（成功失败都）即删。"""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "只支持 http(s) 音频直链"}

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMP_DIR / f"asr_url_{uuid.uuid4().hex[:8]}{_suffix(url)}"
    try:
        await _download(url, dest)
        return await transcribe_file(dest, max_minutes)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        dest.unlink(missing_ok=True)


def _suffix(url: str) -> str:
    tail = url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    if "." in tail:
        ext = tail.rsplit(".", 1)[-1].lower()
        if ext.isalnum() and len(ext) <= 5:
            return "." + ext
    return ".audio"


async def _download(url: str, dest: Path) -> None:
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.get(url, headers={"User-Agent": UA}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"音频下载 HTTP {resp.status}")
            total = 0
            with dest.open("wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)
                    total += len(chunk)
                    if total > MAX_AUDIO_MB * 1024 * 1024:
                        raise RuntimeError("音频超过大小上限，放弃")
    if dest.stat().st_size < 1024:
        raise RuntimeError("音频文件为空")
