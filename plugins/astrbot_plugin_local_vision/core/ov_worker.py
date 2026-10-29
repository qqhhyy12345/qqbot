"""OpenVINO VLM worker —— llama-server 的替身。

接口对齐 llama-server（插件零改动调用）：
  GET  /health                -> {"status": "ok"}
  POST /v1/chat/completions   -> OpenAI 格式响应（只实现插件用到的部分）

模型在启动时加载（~7s），进程被插件看门狗杀掉即卸载。
单请求串行处理（VLMPipeline 不支持并发）。

用法: python ov_worker.py --model <ov_model_dir> --port 1235 --device GPU
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_DIR = ""
DEVICE = "GPU"
_lock = threading.Lock()  # VLMPipeline 串行


def build_pipe():
    import openvino_genai as ovg
    import openvino.properties.hint as hints

    config = {hints.performance_mode: hints.PerformanceMode.LATENCY}
    return ovg.VLMPipeline(MODEL_DIR, DEVICE, **config)


def decode_data_uri(uri: str):
    """data:image/jpeg;base64,xxx -> (PIL.Image|None)"""
    try:
        head, b64 = uri.split(",", 1)
        raw = base64.b64decode(b64)
        from PIL import Image

        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None


def extract_request(payload: dict):
    """从 OpenAI chat 格式里拿 (prompt, images, max_tokens)。"""
    prompt = ""
    images = []
    max_tokens = 160
    for msg in payload.get("messages", []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            prompt += content
            continue
        for part in content or []:
            if part.get("type") == "text":
                prompt += part.get("text", "")
            elif part.get("type") == "image_url":
                img = decode_data_uri((part.get("image_url") or {}).get("url", ""))
                if img is not None:
                    images.append(img)
    max_tokens = int(payload.get("max_tokens") or 160)
    return prompt.strip(), images, max_tokens


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默，日志走 stdout 由插件收
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt, images, max_tokens = extract_request(payload)
            if not images:
                self._json(400, {"error": "no image"})
                return

            import numpy as np
            import openvino as ov

            with _lock:  # 串行推理
                tensors = [ov.Tensor(np.asarray(im)[None]) for im in images]
                out = self.server.pipe.generate(
                    prompt=prompt, images=tensors, max_new_tokens=max_tokens
                )
            text = str(out.texts[0]).strip()
            self._json(200, {
                "id": "ov-worker",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
            })
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[ov_worker] {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()
            try:
                self._json(500, {"error": str(exc)[:200]})
            except Exception:
                pass


def main():
    global MODEL_DIR, DEVICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=1235)
    ap.add_argument("--device", default="GPU")
    args = ap.parse_args()
    MODEL_DIR = args.model
    DEVICE = args.device

    t0 = time.time()
    pipe = build_pipe()
    load_s = time.time() - t0

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.pipe = pipe
    print(f"[ov_worker] ready in {load_s:.1f}s on :{args.port} ({DEVICE})", flush=True)

    # 就绪信号靠 stdout/health，双保险：进程退出由插件看门狗负责
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
