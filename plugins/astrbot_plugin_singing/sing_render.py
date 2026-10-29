"""DiffSinger 渲染 worker。

由 astrbot_plugin_singing 以子进程方式调用，必须用 singing 的 venv 跑
（D:\\qqbot\\_uv_tools\\singing\\.venv），因为那边的环境才有 torch / onnxruntime。

约定：
- stdin 收一个 JSON 请求，读完即渲染
- stdout 只吐一行 ``RESULT:{json}``，模型自己的打印全部改道 stderr
"""

import contextlib
import json
import re
import sys
import time
from pathlib import Path

_REAL_OUT = sys.stdout

_SCALE = {"1": 0, "2": 2, "3": 4, "4": 5, "5": 7, "6": 9, "7": 11}
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_TOKEN = re.compile(r"^([#b]?)([0-7])((?:[',])*)$")
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()《》<>…—~·\-]+")

# opencpop 字典把「也」记成 y E、「眼」记成 y En（[ɛ] 系列），
# 但绮萱这个声库只收小写，把这两个韵母并进了 ie / ian。
_PHONEME_ALIAS = {"E": "ie", "En": "ian"}

MAX_SECONDS = 58.0  # QQ 语音消息上限 60 秒，留一点余量


def parse_base(text: str) -> int:
    """``C4`` -> 60。简谱的 1 对应这个音。"""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", (text or "C4").strip())
    if not m:
        raise ValueError(f"调号看不懂：{text}（要像 C4 / D4 / G3）")
    name, acc, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    midi = (octave + 1) * 12 + _PITCH_CLASS[name]
    if acc == "#":
        midi += 1
    elif acc == "b":
        midi -= 1
    return midi


def parse_melody(text: str, base_midi: int, beat: float) -> list[list]:
    """简谱 -> [[midi, 拍数], ...]。``-`` 给前一个音续一拍。"""
    notes: list[list] = []
    for tok in text.replace("|", " ").split():
        if tok == "-":
            if not notes:
                raise ValueError("简谱开头不能是延音线 -")
            notes[-1][1] += beat
            continue
        m = _TOKEN.match(tok)
        if not m:
            raise ValueError(f"看不懂的简谱记号：{tok}")
        acc, deg, octs = m.groups()
        if deg == "0":
            raise ValueError("暂不支持休止符 0")
        midi = base_midi + _SCALE[deg]
        if acc == "#":
            midi += 1
        elif acc == "b":
            midi -= 1
        for ch in octs:
            midi += 12 if ch == "'" else -12
        notes.append([midi, beat])
    if not notes:
        raise ValueError("简谱是空的")
    return notes


def split_lyrics(text: str) -> list[str]:
    """去掉标点空格，一个字一个音节。"""
    chars = list(_PUNCT.sub("", text or ""))
    if not chars:
        raise ValueError("歌词是空的")
    bad = [c for c in chars if not "\u4e00" <= c <= "\u9fff"]
    if bad:
        raise ValueError(f"目前只支持中文歌词，这几个字不认识：{''.join(bad[:6])}")
    return chars


def midi_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


DML_DIR = Path(r"D:\qqbot\_uv_tools\singing\dml_runtime")


def _use_gpu() -> str:
    """把 ONNX 后端切到 DirectML，返回实际用的后端名。

    本机只有 Iris Xe 核显，没有 CUDA，Windows 上能用的就是 DirectML。
    directml 那套是单独装在 dml_runtime 里的（1.24.4），不覆盖 venv 里的
    onnxruntime 1.31.1，靠 sys.path 抢先命中；删掉这个目录就退回 CPU。
    """
    if not DML_DIR.exists():
        return "cpu"
    if str(DML_DIR) not in sys.path:
        sys.path.insert(0, str(DML_DIR))

    import onnxruntime as ort

    if "DmlExecutionProvider" not in ort.get_available_providers():
        return "cpu"

    orig = ort.InferenceSession

    def make(path, sess_options=None, providers=None, **kw):
        # 声码器是纯卷积、模型小，核显上来回搬运反而比 CPU 慢三成，钉在 CPU 上
        if "dsvocoder" in str(path).lower():
            return orig(
                path, sess_options=sess_options, providers=["CPUExecutionProvider"], **kw
            )
        try:
            return orig(
                path,
                sess_options=sess_options,
                providers=["DmlExecutionProvider", "CPUExecutionProvider"],
                **kw,
            )
        except Exception as exc:  # noqa: BLE001 - 某个模型算子不支持就单独退回 CPU
            print(f"[gpu] {Path(path).name} 上不了 DirectML，退回 CPU：{exc}", file=sys.stderr)
            return orig(
                path, sess_options=sess_options, providers=["CPUExecutionProvider"], **kw
            )

    ort.InferenceSession = make
    return "dml"


def _load_chinese_phonemes(voice_bank: Path) -> set[str]:
    """声库真正的中文音素表（去掉 zh/ 前缀）。空集表示查不到，跳过校验。"""
    for path in sorted(voice_bank.glob("*.phonemes.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        return {k[3:] for k in data if isinstance(k, str) and k.startswith("zh/")}
    return set()


def _fix_phonemes(phones: list[str], known: set[str]) -> list[str]:
    """字典给漏了的音素按别名兜回去；兜不回去就明说哪个音没有。"""
    fixed = []
    for phone in phones:
        if not known or phone in known:
            fixed.append(phone)
        elif _PHONEME_ALIAS.get(phone) in known:
            fixed.append(_PHONEME_ALIAS[phone])
        else:
            raise ValueError(f"音素 {phone} 这个声库没有，也没有对应别名")
    return fixed


def render(req: dict) -> dict:
    # 必须在 diffsinger 之前决定后端：它一旦 import 了 cpu 版 onnxruntime 就换不掉了
    device = _use_gpu() if req.get("device", "gpu") != "cpu" else "cpu"

    import numpy as np
    import soundfile as sf
    from diffsinger_utau.voice_bank import PredAll
    from diffsinger_utau.voice_bank.commons.ds_reader import DSReader, DSTextReplacer

    voice_bank = Path(req["voice_bank"])
    out_dir = Path(req["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    known_phones = _load_chinese_phonemes(voice_bank)

    base_midi = parse_base(req.get("base", "C4"))
    beat = 60.0 / float(req.get("tempo", 120))
    notes = parse_melody(req["melody"], base_midi, beat)
    chars = split_lyrics(req["lyrics"])

    if len(notes) != len(chars):
        raise ValueError(
            f"歌词 {len(chars)} 个字、简谱 {len(notes)} 个音，对不上"
            f"（一个字唱一个音，延音用 - 表示）"
        )

    shift = int(req.get("key_shift", 0))

    # 按字切音素：Phome 的启发式会把「一」单独成组再和下一个字粘连，
    # 导致音节数和音符数对不上，所以这里逐字查字典、逐字记数。
    replacer = DSTextReplacer()
    per_char: list[list[str]] = []
    for ch in chars:
        phones = replacer.get_phonemes(ch, replacer.opencpop_dict)
        if not phones:
            raise ValueError(f"「{ch}」切不出拼音")
        try:
            per_char.append(_fix_phonemes(phones, known_phones))
        except ValueError as exc:
            raise ValueError(f"「{ch}」：{exc}") from None

    section = DSReader.DSSection(
        {
            "offset": 0.0,
            "text": " ".join(chars),
            "ph_seq": " ".join(p for phones in per_char for p in phones),
            "ph_num": " ".join(str(len(phones)) for phones in per_char),
            "note_seq": " ".join(midi_name(n[0] + shift) for n in notes),
            "note_dur": " ".join(f"{n[1]:.3f}" for n in notes),
            "note_slur": " ".join("0" for _ in notes),
        }
    )

    t_load = time.time()
    predictor = PredAll(voice_bank)
    load_sec = time.time() - t_load

    # 声库 mel 是自然对数（dsconfig: mel_base e），随包的 ONNX 声码器按 log10 训练
    orig_predict = predictor.pred_vocoder.predict

    def predict_log10(mel, f0=None):
        return orig_predict(mel / np.log(10.0), f0)

    predictor.pred_vocoder.predict = predict_log10

    t_render = time.time()
    result = predictor.predict_full_pipeline(
        ds=section,
        lang="zh",
        speaker=None,
        key_shift=0,  # 移调已经算进 note_seq 了
        pitch_steps=int(req.get("pitch_steps", 10)),
        variance_steps=int(req.get("variance_steps", 10)),
        acoustic_steps=int(req.get("acoustic_steps", 20)),
        gender=float(req.get("gender", 0.0)),
        output_dir=str(out_dir),
        save_intermediate=False,
    )
    render_sec = time.time() - t_render

    wav_path = Path(result["audio_path"])
    data, sr = sf.read(str(wav_path))
    duration = len(data) / float(sr)
    if duration > MAX_SECONDS:
        raise ValueError(f"唱出来 {duration:.1f} 秒，超过语音消息上限，把歌词缩短点")

    mp3_path = wav_path.with_suffix(".mp3")
    sf.write(str(mp3_path), data, sr, format="MP3", subtype="MPEG_LAYER_III")
    wav_path.unlink(missing_ok=True)

    return {
        "ok": True,
        "mp3": str(mp3_path),
        "device": device,
        "duration": round(duration, 2),
        "syllables": len(chars),
        "load_sec": round(load_sec, 1),
        "render_sec": round(render_sec, 1),
    }


def main() -> None:
    try:
        req = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            payload = render(req)
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    _REAL_OUT.write("RESULT:" + json.dumps(payload, ensure_ascii=False) + "\n")
    _REAL_OUT.flush()


if __name__ == "__main__":
    main()
