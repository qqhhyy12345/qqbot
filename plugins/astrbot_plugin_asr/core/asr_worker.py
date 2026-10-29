"""本地 ASR worker —— 由插件按调用拉起，干完活即退出。

协议：stdin 收一行 JSON 任务，stdout 回一行 JSON 结果。
  任务: {"audio_path": "...", "max_minutes": 5}
  结果: {"ok": true, "text": "...", "duration_s": 123}
       / {"ok": false, "error": "..."}

内部流程：
  1. av(Python 绑定的 ffmpeg) 解码任意音频容器 -> 16k mono float32
  2. faster-whisper small（CT2，int8 CPU）语音转文字
  3. opencc t2s 把繁体转成简体（whisper 中文爱出繁体）

用法: python asr_worker.py <model_dir>
"""

from __future__ import annotations

import json
import sys

MODEL_DIR = ""  # faster-whisper 模型目录（CT2 格式）


def decode_audio(path: str, max_minutes: float) -> tuple["np.ndarray", float]:
    """任意音频容器 -> 16k mono float32，最长截取 max_minutes 分钟。"""
    import av
    import numpy as np

    container = av.open(path)
    stream = next((s for s in container.streams if s.type == "audio"), None)
    if stream is None:
        raise ValueError("文件里没有音频流")

    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks: list = []
    limit = max_minutes * 60 * 16000
    total = 0
    for frame in container.decode(stream):
        for rf in resampler.resample(frame):
            arr = rf.to_ndarray().flatten()
            chunks.append(arr)
            total += arr.size
            if total >= limit:
                break
        if total >= limit:
            break
    container.close()
    if not chunks:
        raise ValueError("音频解码结果为空")
    audio = np.concatenate(chunks)[: int(limit)].astype("float32")
    # 简单归一化，防增益过低影响 whisper
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio *= min(1.0 / peak, 4.0)
    return audio, total / 16000.0


def transcribe(audio) -> str:
    from faster_whisper import WhisperModel

    # int8 CPU：whisper-small 在 14 线程上约 5~10 倍实时
    model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8", cpu_threads=14)
    segments, _info = model.transcribe(audio, language="zh", beam_size=1, vad_filter=True)
    parts = []
    for seg in segments:
        t = seg.text.strip()
        # 纯音乐/哼唱段 whisper 会输出乱码或省略号，过滤
        if t and t not in {"…", "...", "。"}:
            parts.append(t)
    text = " ".join(parts).strip()
    if text:
        try:  # whisper 中文爱出繁体，转简体
            from opencc import OpenCC
            text = OpenCC("t2s").convert(text)
        except Exception:  # noqa: BLE001
            pass
    return text


def main() -> None:
    global MODEL_DIR
    # stdout 默认走 Windows 控制台编码（GBK），中文转写结果会被父进程按 UTF-8 解掉。
    # 协议两端固定 UTF-8；stdin 也改，音频路径里可能有中文。
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    MODEL_DIR = args[0] if args else r"D:/qqbot/models/faster-whisper-small"

    job = json.loads(sys.stdin.readline())
    try:
        audio, dur = decode_audio(job["audio_path"], float(job.get("max_minutes", 5)))
        text = transcribe(audio)
        print(json.dumps({"ok": True, "text": text, "duration_s": round(dur)},
                         ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
