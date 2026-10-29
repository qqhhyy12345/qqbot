"""用本机 yolov8n 跑 QQ 空间说说里的真实图片，看看它到底能给出什么。

只读：不改任何文件，不做检测结果落盘。
"""

import glob
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Users\<user>\Desktop\ultralytics-main")

from ultralytics import YOLO  # noqa: E402

WEIGHT = r"C:\Users\<user>\Desktop\ultralytics-main\yolov8n.pt"
CACHE = r"D:\qqbot\astrbot\data\plugin_data\astrbot_plugin_qzone\cache\builtin_renderer"

print("加载模型:", WEIGHT)
model = YOLO(WEIGHT)
print("模型类别数:", len(model.names))

files = sorted(glob.glob(os.path.join(CACHE, "image_*.jpg")))[:4]
print(f"待测图片: {len(files)} 张\n")

for path in files:
    result = model(path, verbose=False, conf=0.25)[0]
    h, w = result.orig_shape
    found = {}
    for box in result.boxes:
        name = model.names[int(box.cls[0])]
        found[name] = max(found.get(name, 0.0), float(box.conf[0]))
    print(f"--- {os.path.basename(path)}  ({w}x{h})")
    if found:
        top = sorted(found.items(), key=lambda x: -x[1])
        print("    物体: " + ", ".join(f"{k} {v:.2f}" for k, v in top))
    else:
        print("    物体: （一个都没检测到）")
    print()
