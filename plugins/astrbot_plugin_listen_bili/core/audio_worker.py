"""B 站音频分析 worker —— 由插件按调用拉起，干完活即退出。

协议：stdin 收一行 JSON 任务，stdout 回一行 JSON 结果。
  任务: {"audio_path": "...", "max_minutes": 5}
  结果: {"ok": true, "text": "...", "duration_s": 123, "music": {...}}
       / {"ok": false, "error": "..."}

内部流程：
  1. av(Python 绑定的 ffmpeg) 解码 m4s/mp4 音频 -> 16k mono float32
  2. openvino-genai WhisperPipeline（GPU）语音转文字
  3. librosa 旋律分析：BPM / 调性 / 音域 / 旋律走向（纯 DSP，无额外模型）

用法: python audio_worker.py < models/in > models/out
模型目录固定由插件传入（--model），设备 --device。
"""

from __future__ import annotations

import json
import sys

MODEL_DIR = ""      # faster-whisper 模型目录（CT2 格式）
DEVICE = "cpu"


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


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def analyze_melody(audio, sr: int = 16000) -> dict:
    """BPM / 调性 / 音域 / 走向。全 DSP，几秒内出结果。"""
    import numpy as np
    import librosa

    out: dict = {}
    # 节拍
    onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
    tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
    out["bpm"] = round(float(np.atleast_1d(tempo)[0]), 1)

    # 调性（chroma 平均 vs Krumhansl 模板）
    chroma = librosa.feature.chroma_cqt(y=audio, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    best, best_r = None, -2.0
    for shift in range(12):
        v = np.roll(chroma_mean, shift)
        for name, prof in (("大调", MAJOR_PROFILE), ("小调", MINOR_PROFILE)):
            r = float(np.corrcoef(v, prof)[0, 1])
            if r > best_r:
                best_r, best = r, (NOTE_NAMES[shift], name)
    out["key"] = f"{best[0]} {best[1]}"

    # 音高轮廓（piptrack 便宜；每秒取一个中位点描述走向）
    pitches, mags = librosa.piptrack(y=audio, sr=sr, fmin=80, fmax=1000)
    conf = (mags > mags.max() * 0.1) & (pitches > 0)
    f0 = np.where(conf, pitches, np.nan)
    seg = sr // 2  # 半秒一帧窗
    n_seg = len(audio) // seg
    points = []
    for i in range(n_seg):
        seg_f0 = f0[:, i * 2 : i * 2 + 2]
        vals = seg_f0[~np.isnan(seg_f0)]
        if vals.size >= 3:
            points.append(float(np.median(vals)))
    if points:
        arr = np.array(points)
        lo, hi = float(np.percentile(arr, 10)), float(np.percentile(arr, 90))
        midi_lo, midi_hi = 69 + 12 * np.log2(max(lo, 1) / 440), 69 + 12 * np.log2(max(hi, 1) / 440)
        out["range"] = f"{NOTE_NAMES[int(round(midi_lo)) % 12]}{int(round(midi_lo)) // 12 - 1} ~ {NOTE_NAMES[int(round(midi_hi)) % 12]}{int(round(midi_hi)) // 12 - 1}"
        # 走向：把序列粗分成 8 段，各段中位 -> 上行/下行/平稳
        k = max(2, len(arr) // 8)
        moves = []
        for i in range(0, len(arr) - k, k):
            d = float(np.median(arr[i + k : i + 2 * k])) - float(np.median(arr[i : i + k]))
            moves.append("升" if d > 1.0 else ("降" if d < -1.0 else "平"))
        out["contour"] = "-".join(moves[:12])
        out["has_melody"] = bool(midi_hi - midi_lo > 3)  # 音域 >3 半音才算有旋律
    return out


def main() -> None:
    global MODEL_DIR, DEVICE
    args = sys.argv[1:]
    MODEL_DIR = args[0] if args else r"D:/qqbot/models/whisper-small-ov"
    DEVICE = args[1] if len(args) > 1 else "GPU"

    job = json.loads(sys.stdin.readline())
    try:
        audio, dur = decode_audio(job["audio_path"], float(job.get("max_minutes", 5)))
        text = transcribe(audio)
        try:
            music = analyze_melody(audio)
        except Exception as exc:  # noqa: BLE001 旋律分析挂了不影响转写
            music = {"error": f"{type(exc).__name__}: {exc}"}
        # 说话为主的视频旋律特征没意义（BPM/调性对着人声算出来的都是噪声）
        if len(text) > 150:
            music = {}
        print(json.dumps({"ok": True, "text": text, "duration_s": round(dur), "music": music},
                         ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
