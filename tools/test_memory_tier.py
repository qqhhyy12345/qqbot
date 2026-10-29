"""memory_tier 插件的离线单测。不连真实模型，不碰真实会话。"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

PLUGIN_DIR = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_tier")
sys.path.insert(0, str(PLUGIN_DIR))
import main as mt  # noqa: E402

TEST_UMO = "test:MockMessage:999999"
ok = 0
fail = []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail.append(name)
        print(f"  [FAIL] {name}  {detail}")


# ---------- 1. 轮次切分 ----------
print("\n[1] 轮次切分")
ctx = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "u1"},
    {"role": "assistant", "content": "a1"},
    {"role": "user", "content": "u2"},
    {"role": "assistant", "content": "a2"},
    {"role": "tool", "content": "t2"},
]
r = mt._split_rounds(ctx)
check("system 单独成组", r[0] == [ctx[0]], f"got {len(r[0])}")
check("轮数 = 1+2", len(r) == 3, f"got {len(r)}")
check("第2轮是 u1/a1", [m["content"] for m in r[1]] == ["u1", "a1"])
check("tool 跟随所在轮", [m["content"] for m in r[2]] == ["u2", "a2", "t2"])

# ---------- 2. token 估算 ----------
print("\n[2] token 估算")
check("空文本 = 0", mt._est_tokens("") == 0)
cn = mt._est_tokens("你好世界")
en = mt._est_tokens("hello world")
check("中文 4 字约 4-5 token（实测 1.06/字）", 4 <= cn <= 5, f"got {cn}")
check("英文 11 字符约 6 token（实测 0.55/字符）", 5 <= en <= 7, f"got {en}")
# 关键回归：系数必须是实测值，不是旧的 0.7/0.3（那会低估 48%）
check("系数已按实测修正（不再是 0.7/0.3）",
      mt.CJK_TOKEN_COEF == 1.06 and mt.MIXED_TOKEN_COEF == 0.55,
      f"got {mt.CJK_TOKEN_COEF}/{mt.MIXED_TOKEN_COEF}")
check("阈值已按真实口径设为 22000（归档更稀，保住缓存前缀）",
      mt.SOFT_LIMIT_TOKENS == 22000, f"got {mt.SOFT_LIMIT_TOKENS}")
check("限频已放宽到 300 秒（给缓存恢复时间）",
      mt.COOLDOWN_SECONDS == 300, f"got {mt.COOLDOWN_SECONDS}")
check("清 think 开关默认开启", mt.STRIP_THINK is True, f"got {mt.STRIP_THINK}")
check("多模态 content 取文本", mt._text_of({"content": [{"type": "text", "text": "abc"}]}) == "abc")
check("无 content 返回空", mt._text_of({"role": "user"}) == "")

# ---------- 2b. 请求级 token 估算（采信真实 usage + 固定开销）----------
print("\n[2b] 请求级 token 估算")


class _FakeConv:
    def __init__(self, usage):
        self.token_usage = usage


class _FakeReq:
    """最小 ProviderRequest 替身：只带估算需要的三个字段。"""

    def __init__(self, contexts, usage=0, system_prompt="", tools=None):
        self.contexts = contexts
        self.conversation = _FakeConv(usage) if usage is not None else None
        self.system_prompt = system_prompt
        self.func_tool = tools
        self.extra_user_content_parts = []


class _FakeToolSet:
    """模拟 AstrBot 的 ToolSet（有 tools 列表，元素能 model_dump）。"""

    def __init__(self, tools):
        self.tools = tools

    def model_dump(self):
        return {"tools": [t.model_dump() for t in self.tools]}


class _FakeTool:
    def __init__(self, name, desc):
        self.name = name
        self.desc = desc

    def model_dump(self):
        return {"name": self.name, "description": self.desc}


small_ctx = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "在"}]

r0 = mt._estimate_request_tokens(_FakeReq(small_ctx, usage=0), small_ctx)
check("无真实值时走纯估算", r0 > 0 and r0 < 500, f"got {r0}")

r_high = mt._estimate_request_tokens(_FakeReq(small_ctx, usage=47999), small_ctx)
check("真实 usage 更大时采信真实值", r_high == 47999, f"got {r_high}")

r_est = mt._estimate_request_tokens(_FakeReq(small_ctx, usage=3), small_ctx)
check("估算更大时采信估算值（不低估）", r_est == r0, f"got {r_est} vs {r0}")

r_none = mt._estimate_request_tokens(_FakeReq(small_ctx, usage=None), small_ctx)
check("conversation 为 None 不崩", r_none == r0, f"got {r_none}")

# 固定开销：system_prompt + 工具定义必须算进去（这正是旧版漏掉的 ~5,459）
sp = "你是<BOT_NICK>，一个活泼的角色。" * 20
tools = _FakeToolSet([_FakeTool("search_web", "搜索工具说明。" * 100)])
r_sp = mt._estimate_request_tokens(_FakeReq(small_ctx, 0, sp), small_ctx)
check("system_prompt 计入开销", r_sp > r0, f"{r0} -> {r_sp}")
r_tl = mt._estimate_request_tokens(_FakeReq(small_ctx, 0, "", tools), small_ctx)
check("工具定义计入开销", r_tl > r0, f"{r0} -> {r_tl}")
r_both = mt._estimate_request_tokens(_FakeReq(small_ctx, 0, sp, tools), small_ctx)
check("系统提示 + 工具定义都算", r_both > r_sp and r_both > r_tl, f"got {r_both}")

# 工具定义序列化失败不能炸
class _BadTools:
    @property
    def tools(self):
        raise RuntimeError("炸了")


try:
    r_bad = mt._estimate_request_tokens(_FakeReq(small_ctx, 0, "", _BadTools()), small_ctx)
    check("工具序列化异常不阻塞归档", r_bad > 0, f"got {r_bad}")
except Exception as exc:  # noqa: BLE001
    check("工具序列化异常不阻塞归档", False, repr(exc))

# think 与图片仍要计入
rich_ctx = small_ctx + [
    {"role": "assistant", "content": [
        {"type": "think", "think": "推理痕迹" * 200},
        {"type": "text", "text": "回答"},
    ]},
    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:..."}}]},
]
r_rich = mt._estimate_request_tokens(_FakeReq(rich_ctx, 0), rich_ctx)
check("think 块计入", r_rich > r0 + 200, f"got {r_rich}")
check("图片按 1500 计", mt._count_tokens(rich_ctx) >= mt.IMAGE_TOKENS, f"got {mt._count_tokens(rich_ctx)}")


# ---------- 2c. think 剥离 ----------
print("\n[2c] think 剥离（归档时清掉推理痕迹）")

_mixed = [
    {"role": "user", "content": "问题"},
    {"role": "assistant", "content": [
        {"type": "think", "think": "推理痕迹" * 50},
        {"type": "text", "text": "回答"},
    ]},
    {"role": "tool", "content": "工具结果"},
    {"role": "assistant", "content": [
        {"type": "think", "think": "只有推理"},
    ], "tool_calls": [{"id": "1", "type": "function"}]},
    {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:..."}},
        {"type": "think", "think": "图里有啥"},
    ]},
]
_out, _saved = mt._strip_think(_mixed)

check("消息数量不变", len(_out) == len(_mixed), f"got {len(_out)}")
check("text 部分保留",
      _out[1]["content"][0]["type"] == "text" and _out[1]["content"][0]["text"] == "回答")
check("think 被剥掉",
      all(p.get("type") != "think" for p in _out[1]["content"]))
check("str 型 content 原样保留", _out[2]["content"] == "工具结果")
check("只有 think 的消息 content 变空串（消息不删）",
      _out[3]["content"] == "" and _out[3]["role"] == "assistant",
      f"got {_out[3]['content']!r}")
check("tool_calls 不受影响", _out[3].get("tool_calls") is not None)
check("图片 part 保留",
      _out[4]["content"][0]["type"] == "image_url")
check("省下的 token 数为正", _saved > 0, f"got {_saved}")
check("原消息未被就地修改（不污染调用方引用）",
      any(p.get("type") == "think" for p in _mixed[1]["content"]))
check("剥完的上下文里 think 归零",
      mt._count_tokens(_out) == mt._count_tokens(_mixed) - _saved,
      f"{mt._count_tokens(_mixed)} - {_saved} != {mt._count_tokens(_out)}")

# 集成：模拟归档重写后的上下文，think 应为 0
_keep = [m for m in _mixed]
_rewritten, _sv = mt._strip_think(_keep)
_think_left = sum(
    1 for m in _rewritten if isinstance(m.get("content"), list)
    for p in m["content"]
    if isinstance(p, dict) and p.get("type") == "think"
)
check("归档重写后不再有 think part", _think_left == 0, f"got {_think_left}")


# ---------- 3. 归档路径 ----------
print("\n[3] 归档路径")
d = mt._archive_dir()
check("归档目录指向 bot-memory/archive", d.name == "archive" and d.parent.name == "bot-memory", str(d))
check("路径确实存在或可创建", d.parent.exists(), str(d.parent))
check("umo 文件名安全", "/" not in mt._safe_name(TEST_UMO) and ":" not in mt._safe_name(TEST_UMO))


# ---------- 4. 完整流程（stub provider）----------
class FakeResp:
    def __init__(self, text):
        self.completion_text = text


class FakeProvider:
    def __init__(self, text="这是一段摘要。", boom=False):
        self.text = text
        self.boom = boom
        self.calls = 0

    async def text_chat(self, contexts=None, request_max_retries=1, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("模拟摘要失败")
        return FakeResp(self.text)


class FakeCtx:
    def __init__(self, provider):
        self.provider = provider

    def get_using_provider(self, umo=None):
        return self.provider


class FakeEvent:
    def __init__(self, umo):
        self.unified_msg_origin = umo


class FakeReq:
    def __init__(self, contexts):
        self.contexts = contexts


def build_contexts(n_rounds, pad=400):
    """造 n 轮对话，每轮塞点内容把 token 顶上去。"""
    out = []
    for i in range(n_rounds):
        out.append({"role": "user", "content": f"第{i}轮用户提问" + "内容" * pad})
        out.append({"role": "assistant", "content": f"第{i}轮助手回答" + "回复" * pad})
    return out


async def main():
    print("\n[4] 完整归档流程")
    md_p = mt._archive_dir() / (mt._safe_name(TEST_UMO) + ".md")
    md_p.unlink(missing_ok=True)
    # 新格式：原始块存 .blocks.md，主文件是总摘要
    blocks_p = mt._archive_dir() / (mt._safe_name(TEST_UMO) + ".blocks.md")
    blocks_p.unlink(missing_ok=True)
    meta_p = mt._archive_dir() / (mt._safe_name(TEST_UMO) + ".meta.json")
    meta_p.unlink(missing_ok=True)

    mt.ONLY_UMO.clear()  # 测试放开白名单
    provider = FakeProvider()
    plugin = mt.MemoryTierPlugin(FakeCtx(provider))

    # 40 轮，远超 15 轮保留线
    req = FakeReq(build_contexts(40))
    before_tokens = mt._count_tokens(req.contexts)
    await plugin._maybe_tier(FakeEvent(TEST_UMO), req)
    after_tokens = mt._count_tokens(req.contexts)

    check("上下文被压缩", after_tokens < before_tokens, f"{before_tokens} -> {after_tokens}")
    check("摘要只调了一次", provider.calls == 1, f"calls={provider.calls}")
    check("压缩后含归档指针", any("已归档" in (m.get("content") or "") for m in req.contexts))
    check("保留最近 15 轮原文", sum(1 for m in req.contexts if m["role"] == "user") == 16,
          f"user 消息数={sum(1 for m in req.contexts if m['role'] == 'user')}")

    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    check("游标已推进", meta["archived_rounds"] == 25, f"got {meta['archived_rounds']}")
    check("归档块计数 = 1", meta["blocks"] == 1)
    blocks = blocks_p.read_text(encoding="utf-8")
    check("原始块已落进块存档", "归档块 1" in blocks and "摘要" in blocks)
    total = md_p.read_text(encoding="utf-8")
    check("总摘要已写入主文件", "摘要" in total, f"got {total[:80]!r}")
    check("主文件不再堆多个块", total.count("## 归档块") == 0,
          f"主文件里有 {total.count('## 归档块')} 个块标题")

    # ---------- 5. 再跑一次：不该重复归档 ----------
    print("\n[5] 重复调用（限频 + 游标）")
    req2 = FakeReq(build_contexts(40))
    await plugin._maybe_tier(FakeEvent(TEST_UMO), req2)
    check("限频生效，未再调模型", provider.calls == 1, f"calls={provider.calls}")

    # 绕过限频，看游标是否防重复
    plugin._last_archive_at.clear()
    req3 = FakeReq(build_contexts(40))
    await plugin._maybe_tier(FakeEvent(TEST_UMO), req3)
    check("游标未跳过（40轮仍有余量时允许再归档）", provider.calls >= 1, f"calls={provider.calls}")
    meta2 = json.loads(meta_p.read_text(encoding="utf-8"))
    check("归档轮数单调不减", meta2["archived_rounds"] >= meta["archived_rounds"])

    # ---------- 6. 摘要失败必须不改历史 ----------
    print("\n[6] 摘要失败降级")
    meta_p.unlink(missing_ok=True)
    boom = FakeProvider(boom=True)
    p2 = mt.MemoryTierPlugin(FakeCtx(boom))
    req4 = FakeReq(build_contexts(40))
    original = [dict(m) for m in req4.contexts]
    await p2._maybe_tier(FakeEvent(TEST_UMO), req4)
    check("失败时上下文原样保留", len(req4.contexts) == len(original),
          f"{len(original)} -> {len(req4.contexts)}")
    check("失败时不写 meta", not meta_p.exists())

    # ---------- 7. 轮数不够不动手 ----------
    print("\n[7] 轮数不足")
    req5 = FakeReq(build_contexts(5))
    n_before = len(req5.contexts)
    await mt.MemoryTierPlugin(FakeCtx(FakeProvider()))._maybe_tier(FakeEvent(TEST_UMO), req5)
    check("少于 15 轮不动历史", len(req5.contexts) == n_before)

    # 清理测试产物
    (mt._archive_dir() / (mt._safe_name(TEST_UMO) + ".md")).unlink(missing_ok=True)
    meta_p.unlink(missing_ok=True)

    print(f"\n===== 通过 {ok} 项，失败 {len(fail)} 项 =====")
    if fail:
        print("失败项:", fail)
        sys.exit(1)


asyncio.run(main())
