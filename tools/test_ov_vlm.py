# OpenVINO VLMPipeline 大图测试（原图直出，不缩图）
import time, sys
import openvino.properties.hint as hints
import openvino as ov
import openvino_genai as ovg

MODEL_DIR = r"D:/qqbot/models/qwen2vl-2b-ov"
IMAGE = sys.argv[1] if len(sys.argv) > 1 else r"D:/qqbot/data/temp/media_image_20261023140648708_b562.jpg"
DEVICE = sys.argv[2] if len(sys.argv) > 2 else "GPU"

t0 = time.time()
pipe = ovg.VLMPipeline(MODEL_DIR, device=DEVICE)
pipe.start_chat()
print("load %.1fs" % (time.time() - t0))

import numpy as np
from PIL import Image
img = Image.open(IMAGE).convert("RGB")
print("image size:", img.size)

t1 = time.time()
out = pipe.generate(prompt="用一句中文描述这张图片。只输出描述。",
                    images=[ov.Tensor(np.asarray(img)[None])], max_new_tokens=80)
dt = time.time() - t1
print("infer %.1fs ->" % dt, str(out.texts[0])[:80])
pipe.finish_chat()
