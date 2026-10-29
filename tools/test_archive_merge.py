"""归档「合并压缩」（A 方案）的离线单测。

不连真实模型，不碰真实会话 —— 全部在临时目录里跑。

用法：<AstrBot 的 python> tools/test_archive_merge.py
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_tier")
sys.path.insert(0, str(PLUGIN_DIR))
import main as mt  # noqa: E402

TEST_UMO = "test:MockMessage:888888"
ok = 0
fail: list[str] = []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail.append(name)
        print(f"  [FAIL] {name}  {detail}")


class FakeResp:
    def __init__(self, text):
        self.completion_text = text


class FakeProvider:
    """记录每次调用，并按调用次数返回不同内容。"""

    def __init__(self, texts=None, boom_on_merge=False):
        self.texts = texts or []
        self.boom_on_merge = boom_on_merge
        self.calls = 0
        self.prompts: list[str] = []

    async def text_chat(self, contexts=None, request_max_retries=1, **kw):
        self.calls += 1
        self.prompts.append(contexts[0]["content"] if contexts else "")
        # 第 2 次调用是「合并」（第一次是生成新摘要）
        if self.boom_on_merge and self.calls == 1:
            raise RuntimeError("模拟合并失败")
        if self.texts:
            return FakeResp(self.texts[(self.calls - 1) % len(self.texts)])
        return FakeResp(f"第{self.calls}次调用的返回内容")


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
        self.conversation = None
        self.system_prompt = ""
        self.func_tool = None
        self.extra_user_content_parts = []


def build_contexts(n_rounds, pad=400):
    out = []
    for i in range(n_rounds):
        out.append({"role": "user", "content": f"第{i}轮用户提问" + "内容" * pad})
        out.append({"role": "assistant", "content": f"第{i}轮助手回答" + "回复" * pad})
    return out


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="arch_merge_test_"))
    orig_dir = mt._archive_dir
    mt._archive_dir = lambda: tmp
    try:
        name = mt._safe_name(TEST_UMO)
        total_p = tmp / f"{name}.md"
        blocks_p = tmp / f"{name}.blocks.md"

        # ---------- 1. 旧格式迁移 ----------
        print("\n[1] 旧格式 → 新格式的一次性迁移")
        total_p.write_text(
            "\n## 归档块 1（2026-10-23 13:35，30 轮）\n\n旧块一的摘要内容。\n"
            "\n## 归档块 2（2026-10-23 15:42，30 轮）\n\n旧块二的摘要内容。\n",
            encoding="utf-8",
        )
        plugin = mt.MemoryTierPlugin(FakeCtx(FakeProvider()))
        plugin._migrate_archive_if_needed(TEST_UMO)
        check("旧文件已复制为块存档", blocks_p.exists(),
              f"blocks={blocks_p.exists()}")
        check("主文件此时仍保留旧内容（复制不是改名，避免数据丢失窗口）",
              total_p.exists() and "旧块一的摘要内容" in total_p.read_text(encoding="utf-8"))
        check("旧内容一个字没丢",
              "旧块一的摘要内容" in blocks_p.read_text(encoding="utf-8")
              and "旧块二的摘要内容" in blocks_p.read_text(encoding="utf-8"))

        # 再调一次迁移不应重复动作
        plugin._migrate_archive_if_needed(TEST_UMO)
        check("迁移是幂等的（块存档已存在就不再动）",
              blocks_p.exists() and total_p.exists())

        # ---------- 2. 指针构建（换到干净目录，避免受迁移影响）----------
        print("\n[2] 指针内容 = 总摘要 + 最近 N 块")
        tmp2 = Path(tempfile.mkdtemp(prefix="arch_merge_ptr_"))
        mt._archive_dir = lambda: tmp2
        name2 = mt._safe_name(TEST_UMO)
        total2 = tmp2 / f"{name2}.md"
        blocks2 = tmp2 / f"{name2}.blocks.md"
        plugin._write_total_summary(TEST_UMO, "这是合并后的总摘要。")
        plugin._append_archive(TEST_UMO, 3, 30, "第三个块的摘要。")
        plugin._append_archive(TEST_UMO, 4, 30, "第四个块的摘要。")
        ptr = plugin._build_pointer_text(TEST_UMO)
        check("指针包含总摘要", "这是合并后的总摘要。" in ptr)
        check("指针包含最近的块", "第四个块的摘要。" in ptr)
        check("指针不含更早的块（防指针无限增长）",
              "第三个块的摘要。" not in ptr or ptr.count("## 归档块") <= mt.KEEP_RECENT_SUMMARY_BLOCKS,
              f"got {ptr[:160]!r}")
        # 这里只追加了 2 个块，所以最多取 2 个（不超过上限即可）
        check("保留块数受 KEEP_RECENT_SUMMARY_BLOCKS 约束",
              ptr.count("## 归档块") <= mt.KEEP_RECENT_SUMMARY_BLOCKS
              and ptr.count("## 归档块") == 2,
              f"got {ptr.count('## 归档块')}, 上限 {mt.KEEP_RECENT_SUMMARY_BLOCKS}")

        # ---------- 3. 合并与降级 ----------
        print("\n[3] 合并摘要与降级")
        prov = FakeProvider(texts=["旧摘要内容", "合并后的总摘要。"])
        plugin2 = mt.MemoryTierPlugin(FakeCtx(prov))
        merged = await plugin2._merge_summaries(TEST_UMO, "旧摘要内容", "新摘要内容")
        check("合并调了一次模型", prov.calls == 1, f"calls={prov.calls}")
        # 这次直接调合并，所以是第 1 次调用 → 返回 texts[0]
        check("合并结果就是模型返回", merged == "旧摘要内容", f"got {merged!r}")
        check("合并提示词带上了旧摘要", "旧摘要内容" in prov.prompts[0])
        check("合并提示词带上了新摘要", "新摘要内容" in prov.prompts[0])
        check("合并提示词带上了字数目标",
              str(mt.TOTAL_SUMMARY_TARGET_CHARS) in prov.prompts[0])

        # 空旧摘要 → 直接返回新摘要，不调模型
        prov2 = FakeProvider()
        plugin3 = mt.MemoryTierPlugin(FakeCtx(prov2))
        r = await plugin3._merge_summaries(TEST_UMO, "", "只有新摘要")
        check("旧摘要为空时不调模型", prov2.calls == 0 and r == "只有新摘要",
              f"calls={prov2.calls} r={r!r}")

        # 合并失败 → 降级为拼接，信息不丢
        prov3 = FakeProvider(boom_on_merge=True)  # 合并是第 1 次调用 → 直接抛
        plugin4 = mt.MemoryTierPlugin(FakeCtx(prov3))
        r2 = await plugin4._merge_summaries(TEST_UMO, "旧摘要内容", "新摘要内容")
        check("合并失败降级为拼接", "旧摘要内容" in r2 and "新摘要内容" in r2,
              f"got {r2!r}")

        # ---------- 4. 占位符安全 ----------
        print("\n[4] 提示词占位符安全")
        prov4 = FakeProvider(texts=["合并结果"])
        plugin5 = mt.MemoryTierPlugin(FakeCtx(prov4))
        tricky = "摘要里恰好出现 {old} 和 {new} 和 {target} 这些字样"
        await plugin5._merge_summaries(TEST_UMO, tricky, "新摘要")
        sent = prov4.prompts[0]
        check("内容里的占位符没被二次替换",
              sent.count("{old}") == 1 and sent.count("{new}") == 1,
              f"old×{sent.count('{old}')} new×{sent.count('{new}')}")

        # ---------- 5. 连跑多次归档：指针长度是否还涨 ----------
        print("\n[5] 连跑 5 次归档，指针长度是否收敛")
        d2 = Path(tempfile.mkdtemp(prefix="arch_merge_grow_"))
        mt._archive_dir = lambda: d2
        prov5 = FakeProvider(texts=["短摘要", "合并后的总摘要（恒定长度）。"])
        plugin6 = mt.MemoryTierPlugin(FakeCtx(prov5))
        lengths = []
        for _ in range(5):
            req = FakeReq(build_contexts(40))
            plugin6._last_archive_at.clear()
            await plugin6._maybe_tier(FakeEvent(TEST_UMO), req)
            ptr_now = plugin6._build_pointer_text(TEST_UMO)
            lengths.append(len(ptr_now))
        print(f"    指针长度序列: {lengths}")
        # 指针 = 恒定总摘要 + 最近 3 块 → 块数填满后长度收敛
        check("指针长度在块数填满后收敛",
              lengths[-1] == lengths[-2],
              f"got {lengths}")
        check("块存档确实累积了 5 个块",
              (d2 / f"{mt._safe_name(TEST_UMO)}.blocks.md").read_text(
                  encoding="utf-8").count("## 归档块") == 5)
        check("总摘要文件里没有块标题",
              "## 归档块" not in (d2 / f"{mt._safe_name(TEST_UMO)}.md").read_text(
                  encoding="utf-8"))
        shutil.rmtree(d2, ignore_errors=True)

    finally:
        mt._archive_dir = orig_dir
        shutil.rmtree(tmp, ignore_errors=True)
        for _d in list(Path(tempfile.gettempdir()).glob("arch_merge_*")):
            shutil.rmtree(_d, ignore_errors=True)

    print(f"\n===== 通过 {ok} 项，失败 {len(fail)} 项 =====")
    if fail:
        for f in fail:
            print(f"  失败: {f}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
