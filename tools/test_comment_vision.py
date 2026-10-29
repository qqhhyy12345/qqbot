"""验证评说说这条路：本地转述生效时不再把图发给云端。"""

import asyncio
import sys
from types import SimpleNamespace

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

import astrbot_plugin_qzone.core.llm_action as _la
from astrbot_plugin_qzone.core.llm_action import LLMAction
from astrbot_plugin_qzone.core.model import Post


class _AnyProvider(type):
    """让 isinstance(provider, Provider) 恒为真，跳过 AstrBot 的真实类型检查。"""

    def __instancecheck__(cls, obj):
        return True


_la.Provider = _AnyProvider("Provider", (), {})

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


class FakeResp:
    completion_text = "好看！"


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def text_chat(self, **kw):
        self.calls.append(kw)
        return FakeResp()


def make_llm(describe, provider):
    cfg = SimpleNamespace(
        context=None,
        client=None,
        llm=SimpleNamespace(
            comment_prompt="你是一个会评论的朋友", comment_provider_id=None
        ),
    )
    llm = LLMAction(cfg, describe_images=describe)

    async def _ctx(event=None, task_prompt=""):
        return ("sys", [])

    llm._build_request_context = _ctx
    llm._get_provider = lambda pid, event: provider
    return llm


async def run_case(label, describe, expect_images):
    provider = FakeProvider()
    llm = make_llm(describe, provider)
    post = Post(
        tid="t1", text="今天天气不错", name="x",
        images=["http://a/1.jpg", "http://a/2.jpg"],
    )
    try:
        await LLMAction.generate_comment(llm, post=post, event=SimpleNamespace())
    except Exception as exc:
        check(f"{label}: 不抛错", False, f"{type(exc).__name__}: {exc}")
        return

    call = provider.calls[-1]
    got = call.get("image_urls")
    prompt = call.get("prompt", "")
    if expect_images:
        check(f"{label}: 仍带图给云端", bool(got), f"{len(got or [])} 张")
    else:
        check(f"{label}: 不带图给云端", not got, f"image_urls={got}")
        check(f"{label}: 描述进了 prompt", "图片内容" in prompt,
              prompt[-50:].replace("\n", "|"))


async def good(post):
    return ["图里是樱花和河上的小船", "一张显卡包装盒"], 0


async def broken(post):
    raise RuntimeError("模拟本地服务挂掉")


async def useless(post):
    return ["（本地视觉这次没认出来）", "（这张图没下载下来）"], 2


async def main():
    print("=" * 64)
    print("评说说：本地转述 vs 退回云端带图")
    print("=" * 64)
    print("\n[1] 本地转述成功")
    await run_case("本地可用", good, expect_images=False)
    print("\n[2] 本地服务抛异常 -> 退回云端带图")
    await run_case("本地异常", broken, expect_images=True)
    print("\n[3] 本地只给占位结果 -> 退回云端带图")
    await run_case("占位结果", useless, expect_images=True)
    print("\n[4] 没有注入回调（插件未提供）")
    await run_case("无回调", None, expect_images=True)
    print("\n" + "=" * 64)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败: " + ", ".join(FAIL))
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
