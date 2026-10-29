import re

src = open(r"D:/qqbot/snowluma/index.mjs", encoding="utf-8", errors="ignore").read()

# 1) OneBot json 段的构造（两种可能写法）
for pat in (r'type:\s*"json"', r'type:"json"', r"'json'", r'\"json\"'):
    print(pat, "->", len(re.findall(pat, src)))

# 2) 卡片在 wire 解码层：搜 lightApp / richMedia / json 相关解码点
for pat in ("lightApp", "richMedia", "arkStruct", "JsonPush", "elem.json", "jsonMsg"):
    hits = [m.start() for m in re.finditer(pat, src)]
    print(pat, "->", len(hits))
    for h in hits[:2]:
        s = max(0, h - 200)
        print("   ", repr(src[s:h + 220])[:420])
