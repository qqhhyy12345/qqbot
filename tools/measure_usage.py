"""统计「一次提问引发几次 LLM 往返」→ 估算 token 消耗。

要点：
1. 工具结果在日志里是**多行**的，只看 Result: 那一行会严重低估。
2. 每次工具调用都会**重新发一遍完整上下文**，
   所以「往返轮数 × 上下文大小」才是真正的消耗大头。
3. 相邻工具调用（间隔 < 45s）算同一组 = 一次提问引发的一串工具链。

用法：<AstrBot 的 python> tools/measure_usage.py
"""

import io
import re
from collections import defaultdict
from datetime import datetime, timedelta

LOG = r"D:\qqbot\astrbot\data\logs\astrbot.log"
TS = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\]")
TOOL_USE = re.compile(r"使用工具：(\w+)")
TOOL_RESULT = re.compile(r"Tool `(\w+)` Result:")
IN_MSG = re.compile(r"\] \[default\] \[snowluma\(aiocqhttp\)\] (\d+)/\d+:")
GROUP_GAP = 45  # 秒
# 每次工具往返都要重发一遍完整上下文。
# 上下文大小取 memory_tier 归档日志里的实测值。
CTX = 15000  # 保守取归档后的量级（实测 12,696 ~ 17,204）

lines = io.open(LOG, encoding="utf-8", errors="replace").read().splitlines()

recs = []
for l in lines:
    m = TS.search(l)
    recs.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None, l))

# ---- 工具调用事件 + 工具结果块 ----
events = []          # (时间, 类型, 工具名, 结果字符数)
i = 0
while i < len(recs):
    ts, l = recs[i]
    m = TOOL_USE.search(l)
    if m:
        events.append([ts, "use", m.group(1), 0])
    m = TOOL_RESULT.search(l)
    if m:
        # 累计到下一条带时间戳的日志为止
        n = len(l)
        j = i + 1
        while j < len(recs) and recs[j][0] is None:
            n += len(recs[j][1]) + 1
            j += 1
        events.append([ts, "result", m.group(1), n])
    i += 1

# ---- 用户消息 ----
user_msgs = defaultdict(int)   # 时间桶 -> 条数
for ts, l in recs:
    if ts and IN_MSG.search(l):
        user_msgs[ts.replace(minute=0, second=0)] += 1

# ---- 工具链分组 ----
chains = []
cur = []
for ev in events:
    if ev[1] == "use":
        if cur and ev[0] - cur[-1][0] > timedelta(seconds=GROUP_GAP):
            chains.append(cur)
            cur = []
    cur.append(ev)
if cur:
    chains.append(cur)

print("=" * 92)
print("工具链 = 一次提问引发的连续工具调用。每次工具调用 = 一次 LLM 往返（重发全量上下文）")
print("=" * 92)
print(f"{'开始时间':18s} {'链长':>4s} {'工具':34s} {'结果字符':>8s} {'≈token':>7s}")
for ch in chains:
    if not ch:
        continue
    tools = [e[2] for e in ch if e[1] == "use"]
    chars = sum(e[3] for e in ch if e[1] == "result")
    t0 = ch[0][0].strftime("%m-%d %H:%M:%S")
    print(f"{t0:18s} {len(tools):4d} {','.join(tools)[:34]:34s} "
          f"{chars:8d} {chars * 0.69:7.0f}")

print()
print("=" * 92)
print("按小时汇总：工具链数 / 总工具轮数 / 平均链长 / 工具结果 token")
print("=" * 92)
agg = defaultdict(lambda: {"chains": 0, "rounds": 0, "chars": 0, "tools": defaultdict(int)})
for ch in chains:
    if not ch:
        continue
    b = ch[0][0].strftime("%m-%d %H")
    a = agg[b]
    a["chains"] += 1
    a["rounds"] += len([e for e in ch if e[1] == "use"])
    a["chars"] += sum(e[3] for e in ch if e[1] == "result")
    for e in ch:
        if e[1] == "use":
            a["tools"][e[2]] += 1
for b in sorted(agg):
    a = agg[b]
    avg = a["rounds"] / max(a["chains"], 1)
    detail = " ".join(f"{k}×{v}" for k, v in sorted(a["tools"].items()))
    print(f"{b}  链 {a['chains']:3d}  轮 {a['rounds']:3d}  平均 {avg:4.1f}  "
          f"结果 {a['chars']:6d}字 ≈{a['chars'] * 0.69:6.0f}tok  {detail}")

# ---- 各工具的平均单次输出 ----
print()
print("=" * 92)
print("各工具单次调用的平均输出规模（工具结果字节数 → token）")
print("=" * 92)
tc = defaultdict(int)
tn = defaultdict(int)
for ch in chains:
    for e in ch:
        if e[1] == "result":
            tc[e[2]] += e[3]
    for e in ch:
        if e[1] == "use":
            tn[e[2]] += 1
for k in sorted(tc, key=lambda x: -tc[x]):
    n = max(tn[k], 1)
    print(f"  {k:14s} 调用 {tn[k]:3d} 次  平均 {tc[k] // n:6d} 字 "
          f"≈ {tc[k] // n * 0.69:5.0f} token   合计 {tc[k]:6d} 字")

# ---- 改造前后对比：只统计纯搜索类工具链 ----
SEARCH = {"search_web", "fetch_url", "baike_lookup"}


def window(lo, hi, label):
    sel = []
    for ch in chains:
        if not ch:
            continue
        t0 = ch[0][0]
        if not (lo <= t0.strftime("%H:%M") <= hi):
            continue
        tools = [e[2] for e in ch if e[1] == "use"]
        if not tools or not set(tools) & SEARCH:
            continue
        sel.append(ch)
    if not sel:
        print(f"\n{label}: 无数据")
        return
    rounds = sum(len([e for e in c if e[1] == "use"]) for c in sel)
    chars = sum(e[3] for c in sel for e in c if e[1] == "result")
    llm = rounds + len(sel)
    print(f"\n{label}（{len(sel)} 条搜索类工具链）")
    print(f"  工具往返轮数     {rounds:3d}   平均每条链 {rounds / len(sel):.1f} 轮")
    print(f"  LLM 请求总数     {llm:3d}   （工具轮 + 最终回复轮）")
    print(f"  工具结果输出     {chars:6d} 字 ≈ {chars * 0.69:6.0f} token")
    print(f"  上下文重发估算   {llm * CTX:7d} token  （按 {CTX}/轮）")
    print(f"  总输入估算       {llm * CTX + chars * 0.69:7.0f} token")


print()
print("=" * 92)
print("改造前后对比（只算搜索类工具链：search_web / fetch_url / baike_lookup）")
print("=" * 92)
window("19:00", "19:59", "改造前 · 19 点（search_web + fetch_url）")
window("20:29", "21:59", "改造后 · 20:29 之后（search_web + baike_lookup）")
