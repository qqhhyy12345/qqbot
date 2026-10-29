"""验证评论区渲染：她到底能不能读到评论。"""

import sys

sys.path.insert(0, r"D:\qqbot\_uv_tools\astrbot\Lib\site-packages")
sys.path.insert(0, r"D:\qqbot\astrbot\data\plugins")

from astrbot_plugin_qzone.core.model import Comment, Post
from types import SimpleNamespace

from astrbot_plugin_qzone.main import QzonePlugin

FAKE = SimpleNamespace(VIEW_COMMENT_LIMIT=QzonePlugin.VIEW_COMMENT_LIMIT)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def mk(nick, content, parent=None, uin=10001):
    return Comment(uin=uin, nickname=nick, content=content, create_time=0, parent_tid=parent)


def main():
    print("=" * 66)
    print("评论区渲染验证")
    print("=" * 66)

    print("\n[1] 没有评论 → 不产生任何块")
    p = Post(tid="t", text="今天不错", name="x")
    out = QzonePlugin._format_comments(FAKE, p)
    check("空评论返回空串", out == "", repr(out))

    print("\n[2] 单条评论")
    p = Post(tid="t", text="今天不错", name="x", comments=[mk("leosooyaaa", "爱你")])
    out = QzonePlugin._format_comments(FAKE, p)
    print(out.replace("\n", " | "))
    check("出现评论区标题", "【评论区 1 条】" in out)
    check("出现昵称与内容", "leosooyaaa" in out and "爱你" in out)

    print("\n[3] 楼中楼缩进")
    p = Post(
        tid="t", text="x", name="x",
        comments=[mk("小明", "好看"), mk("小红", "确实", parent=12345)],
    )
    out = QzonePlugin._format_comments(FAKE, p)
    print(out.replace("\n", " | "))
    check("主楼用 - ", "- 小明：好看" in out)
    check("楼中楼用 > 缩进", "  > 小红：确实" in out)

    print("\n[4] 超上限截断")
    many = [mk(f"路人{i}", f"第{i}条") for i in range(15)]
    p = Post(tid="t", text="x", name="x", comments=many)
    out = QzonePlugin._format_comments(FAKE, p)
    shown = out.count("：")
    check("只显示 10 条", shown == 10, f"实际 {shown} 条")
    check("标注未展开数量", "另有 5 条未展开" in out)

    print("\n[5] 清理 QQ 表情标签与昵称占位")
    p = Post(
        tid="t", text="x", name="x",
        comments=[mk("阿三", "哈哈哈[em]e100[/em]笑死{nick:小四}你也来")],
    )
    out = QzonePlugin._format_comments(FAKE, p)
    print(out.replace("\n", " | "))
    check("去掉 [em] 标签", "[em]" not in out)
    check("昵称占位被替换掉", "{nick:" not in out and "小四" in out)

    print("\n[6] 空内容评论跳过")
    p = Post(tid="t", text="x", name="x", comments=[mk("某人", "   "), mk("正常", "有内容")])
    out = QzonePlugin._format_comments(FAKE, p)
    check("空内容不计入", out.count("：") == 1, out.replace("\n", " | "))

    print("\n[7] 真实数据：刚拉到的「爱你」那条")
    real = Post(
        tid="8f3e55aaf5b1", text="今天把旧相册翻了一遍。", name="<BOT_NICK>",
        comments=[mk("leosooyaaa", "爱你", uin=123456)],
    )
    out = QzonePlugin._format_comments(FAKE, real)
    print(out.replace("\n", " | "))
    check("真实评论渲染成功", "leosooyaaa：爱你" in out)

    print("\n[8] 拼进完整返回文本的长度")
    full = "今天把旧相册翻了一遍。樱花、寺门、机翼底下压着的雪山。" 
    full += QzonePlugin._format_comments(FAKE, real)
    print(f"  长度 {len(full)} 字 ≈ {int(len(full) * 0.65)} token")
    check("开销可控", len(full) < 300, f"{len(full)} 字")

    print("\n" + "=" * 66)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败: " + ", ".join(FAIL))
    print("=" * 66)


if __name__ == "__main__":
    main()
