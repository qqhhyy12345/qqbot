"""B 站音频的旋律特征 worker —— 只做 DSP：BPM / 调性 / 音域 / 走向。

语音转文字已经拆到 astrbot_plugin_asr，这里只保留"听音乐"的部分。

协议：stdin 收一行 JSON 任务，stdout 回一行 JSON 结果。
  任务: {"audio_path": "...", "max_minutes": 5}
  结果: {"ok": true, "music": {...}, "duration_s": 123}
       / {"ok": false, "error": "..."}

付出的是多一次解码（几秒），换来两个插件互不依赖；whisper 才是耗时大头。
"""

from __future__ import annotations

import json
import sys


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
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio *= min(1.0 / peak, 4.0)
    return audio, total / 16000.0


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

    # 音高轮廓（piptrack 便宜；半秒取一个中位点描述走向）
    pitches, mags = librosa.piptrack(y=audio, sr=sr, fmin=80, fmax=1000)
    conf = (mags > mags.max() * 0.1) & (pitches > 0)
    f0 = np.where(conf, pitches, np.nan)
    seg = sr // 2
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
    # 协议两端固定 UTF-8（Windows 管道默认是 GBK，中文调性名会乱码）
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    job = json.loads(sys.stdin.readline())
    try:
        audio, dur = decode_audio(job["audio_path"], float(job.get("max_minutes", 5)))
        music = analyze_melody(audio)
        print(json.dumps({"ok": True, "music": music, "duration_s": round(dur)},
                         ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
