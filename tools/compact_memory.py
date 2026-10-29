#!/usr/bin/env python3
"""记忆库整合工具 —— memory_beyond 的自动化外壳。

复用插件自带的 build_prompt / parse_operations（与面板「整理记忆」同源），
在其外面加：触发阈值、全量备份、删除保护、软删除归档、变更报告。

默认 dry-run，只出方案不落盘；加 --apply 才真正执行。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = r"D:\qqbot\astrbot\data\plugins\astrbot_plugin_memory_beyond"
MEMORY_ROOT = Path(r"D:\qqbot\bot-memory")
CFG = Path(r"D:\qqbot\astrbot\data\cmd_config.json")
TOOL_DIR = Path(r"D:\qqbot\tools")
REPORT_DIR = TOOL_DIR / "reports"
BACKUP_DIR = MEMORY_ROOT / "_backup"
ARCHIVE_DIR = MEMORY_ROOT / "_archive"

DEFAULT_MIN_INDEX_LINES = 80     # 索引行阈值，见记忆维护规则.md
DEFAULT_MIN_FILES = 80           # 文件数护栏（索引行与文件一般一一对应）
MAX_DELETE_RATIO = 0.4           # 单次删除不超过总数的 40%
ALLOWED_TYPES = {"user", "feedback", "project", "reference"}
PROTECTED_TYPES = {"feedback"}   # 指导类不允许被单纯删除
PROTECTED_NAME_PREFIX = "user-"  # people 作用域里的一人一档

# 全局记忆会被注入到每个人的对话里，出现这些词就是泄漏
PII_PATTERNS = ("<PRIMARY_NAME>", "<OWNER_QQ>", "创建人")

sys.path.insert(0, PLUGIN_DIR)
from core import consolidate, memstore  # noqa: E402


def load_provider() -> tuple[str, str, str]:
    """AstrBot 4.x 把传输参数放在 provider_sources，provider 只引用它。"""
    cfg = json.load(open(CFG, encoding="utf-8-sig"))
    p = (cfg.get("provider") or [])[0]
    src = next(
        (s for s in (cfg.get("provider_sources") or [])
         if s.get("id") == p.get("provider_source_id")),
        None,
    ) or p
    key = src["key"][0] if isinstance(src.get("key"), list) else src.get("key")
    return src["api_base"].rstrip("/"), key, p["model"]


def call_llm(prompt: str) -> str:
    api_base, key, model = load_provider()
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        api_base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def index_line_count(scope) -> int:
    path = scope.root / memstore.INDEX_FILENAME
    if not path.is_file():
        return 0
    text = path.read_text("utf-8", errors="replace")
    return sum(1 for ln in text.splitlines() if ln.strip().startswith("- ["))


def collect_scopes(store: memstore.MemoryStore, which: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    if which in ("all", "global"):
        g = store.global_scope()
        if g.root.is_dir() and g.list_files():
            out.append(("global", g))
    if which in ("all", "session"):
        for key in store.session_keys():
            scope = store.session_scope_by_key(key)
            if scope is not None:
                out.append((f"session/{key}", scope))
    if which in ("all", "people"):
        for key in store.people_keys():
            scope = store.people_scope_by_key(key)
            if scope is not None:
                out.append((f"people/{key}", scope))
    return out


def check_global_privacy(store: memstore.MemoryStore) -> list[str]:
    """全局记忆注入到每个会话的提示词里，任何个人信息放在这里都等于公开。"""
    scope = store.global_scope()
    if not scope.root.is_dir():
        return []
    hits: list[str] = []
    for name in scope.list_files():
        path = scope.root / name
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        # source: 是插件自动写的元数据（记录谁写的），不算正文泄漏
        scan = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("source:")
        )
        for pat in PII_PATTERNS:
            if pat in scan:
                hits.append(f"{name} 含「{pat}」")
    return hits


def validate(ops, headers, scope_label: str) -> tuple[list, list[str], list[str]]:
    """返回 (可执行操作, 拒绝原因, 提示)。保护机制都在这里。"""
    by_name = {h.file: h for h in headers}
    total = max(len(headers), 1)
    accepted, rejected, notes = [], [], []

    writes = [o for o in ops if o.op == "write"]
    deletes = [o for o in ops if o.op == "delete"]
    write_targets = {o.file for o in writes}

    for o in writes:
        if o.type not in ALLOWED_TYPES:
            rejected.append(f"write {o.file}: 类型非法 {o.type!r}")
            continue
        if not o.description.strip():
            rejected.append(f"write {o.file}: description 为空")
            continue
        if not o.content.strip():
            rejected.append(f"write {o.file}: 正文为空")
            continue
        if not memstore.ScopeStore.is_valid_name(o.file):
            rejected.append(f"write {o.file}: 文件名不合规")
            continue
        if scope_label.startswith("global"):
            hit = next(
                (p for p in PII_PATTERNS
                 if p in (o.file + o.description + o.content)),
                None,
            )
            if hit:
                rejected.append(
                    f"write {o.file}: global 里出现「{hit}」，会泄漏给所有会话"
                )
                continue
        accepted.append(o)

    ratio_cap = max(1, int(total * MAX_DELETE_RATIO))
    kept_deletes = 0
    for o in deletes:
        h = by_name.get(o.file)
        if h is None:
            rejected.append(f"delete {o.file}: 文件不存在")
            continue
        if not o.reason.strip():
            rejected.append(f"delete {o.file}: 未说明理由")
            continue
        # 保护 1：people 作用域的一人一档不允许删除
        if scope_label.startswith("people/") and o.file.startswith(PROTECTED_NAME_PREFIX):
            rejected.append(f"delete {o.file}: people 作用域人物档案受保护")
            continue
        # 保护 2：指导类（feedback）不允许被单纯删除
        if (h.type or "").lower() in PROTECTED_TYPES:
            rejected.append(f"delete {o.file}: feedback 类受保护（请合并进其他文件）")
            continue
        # 保护 3：单次删除比例上限
        if kept_deletes >= ratio_cap:
            rejected.append(f"delete {o.file}: 超出单次删除上限 {ratio_cap} 个")
            continue
        # 保护 4：删除必须有去处——同批次里有 write，或理由里指明去向
        if not writes and "合并" not in o.reason:
            rejected.append(f"delete {o.file}: 本批次没有任何写入，禁止纯删除")
            continue
        accepted.append(o)
        kept_deletes += 1

    # 保护 5：合并后若某个 feedback 既被删又没被写回，视为信息丢失
    deleted_feedback = [
        o.file for o in deletes
        if (by_name.get(o.file).type or "").lower() in PROTECTED_TYPES
    ]
    if deleted_feedback and not write_targets:
        notes.append(f"注意：本批次计划删除 {len(deleted_feedback)} 个 feedback 文件但无写回")

    return accepted, rejected, notes


_BACKED_UP = {"done": False}


def ensure_backup(root: Path, stamp: str) -> None:
    """首次真正改动之前做全量备份；一次运行只备一次。"""
    if _BACKED_UP["done"]:
        return
    backup_dir = root / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / stamp
    if not dest.exists():

        def ignore(src, names):
            return [n for n in names if n in ("_backup", "_archive")]

        shutil.copytree(root, dest, ignore=ignore)
        print(f"  [备份] {dest}")
    _BACKED_UP["done"] = True


async def run_scope(scope_label: str, scope, args) -> dict:
    headers = scope.headers()
    files = scope.list_files()
    n_lines = index_line_count(scope)
    result = {
        "scope": scope_label, "files": len(files), "index_lines": n_lines,
        "triggered": False, "ops": [], "rejected": [], "notes": [],
        "applied": False,
    }
    if len(files) == 0:
        result["notes"].append("空作用域，跳过")
        return result
    if not args.force and n_lines < args.min_lines and len(files) < args.min_files:
        result["notes"].append(
            f"未达阈值（索引 {n_lines}/{args.min_lines} 行，文件 {len(files)}/{args.min_files}），跳过"
        )
        return result
    result["triggered"] = True

    docs = []
    for h in headers:
        content = await scope.read(h.file)
        if content:
            docs.append((h, content))
    prompt, skipped = consolidate.build_prompt(scope_label, docs)
    if skipped:
        result["notes"].append(f"超出输入预算未纳入：{', '.join(skipped)}")

    if args.ops_file:
        payload = json.loads(Path(args.ops_file).read_text(encoding="utf-8"))
        ops = consolidate.operations_from_payload(payload, set(files))
        result["notes"].append(f"使用指定操作文件（{len(ops)} 个操作），未调用模型")
    else:
        raw = call_llm(prompt)
        ops = consolidate.parse_operations(raw, set(files))
    if ops is None:
        result["notes"].append("模型输出无法解析为操作数组，本轮放弃（未做任何改动）")
        return result
    if not ops:
        result["notes"].append("模型认为无需整理，未做任何改动")
        return result

    base_mtimes = {h.file: h.mtime for h in headers}
    for o in ops:
        o.base_mtime = base_mtimes.get(o.file, 0.0)

    accepted, rejected, notes = validate(ops, headers, scope_label)
    result["rejected"] = rejected
    result["notes"].extend(notes)
    result["ops"] = [o.to_dict() for o in accepted]

    for o in consolidate.ordered(accepted):
        if consolidate.is_stale(o, scope.mtime(o.file)):
            result["rejected"].append(f"{o.file}: 期间被修改，跳过")
            continue
        if args.apply:
            ensure_backup(Path(args.root), args.stamp)
        if o.op == "write":
            if args.apply:
                rep = await scope.write(o.file, o.render())
                if not rep.ok:
                    result["rejected"].append(f"write {o.file}: {rep.message}")
                    continue
            result["applied"] = result["applied"] or args.apply
        else:
            # 软删除：先归档再移除，磁盘上永不真正消失
            if args.apply:
                content = await scope.read(o.file) or ""
                target = Path(args.root) / "_archive" / args.stamp / scope_label.replace("/", "_")
                target.mkdir(parents=True, exist_ok=True)
                (target / o.file).write_text(content, encoding="utf-8")
                rep = await scope.delete(o.file)
                if not rep.ok:
                    result["rejected"].append(f"delete {o.file}: {rep.message}")
                    continue
            result["applied"] = result["applied"] or args.apply
    return result


async def main() -> None:
    ap = argparse.ArgumentParser(description="记忆库整合")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只出方案）")
    ap.add_argument("--force", action="store_true", help="忽略阈值强制整理")
    ap.add_argument("--min-lines", type=int, default=DEFAULT_MIN_INDEX_LINES)
    ap.add_argument("--min-files", type=int, default=DEFAULT_MIN_FILES)
    ap.add_argument("--scope", choices=["all", "session", "people"], default="all")
    ap.add_argument("--root", default=str(MEMORY_ROOT), help="记忆库根目录（测试用）")
    ap.add_argument("--ops-file", default="", help="用指定的操作 JSON 代替模型输出（测试/复核用）")
    args = ap.parse_args()
    args.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.root)

    store = memstore.MemoryStore(root / "memories")

    leaks = check_global_privacy(store)
    if leaks:
        print("!! 全局记忆里出现个人信息 —— 会被注入到每一个人的对话里：")
        for h in leaks:
            print("   -", h)
        if args.apply:
            print("   已中止执行，先清理 global 作用域再跑。")
            return
        print("   （预演继续，但请尽快清理）\n")

    scopes = collect_scopes(store, args.scope)
    if not scopes:
        print("没有找到任何作用域，退出")
        return

    print(f"{'执行' if args.apply else '预演'} | 作用域 {len(scopes)} 个 | 阈值 索引 {args.min_lines} 行 / 文件 {args.min_files} 个")
    print("-" * 66)

    results = []
    for label, scope in scopes:
        r = await run_scope(label, scope, args)
        results.append(r)
        flag = "触发" if r["triggered"] else "跳过"
        print(f"[{flag}] {label}  文件 {r['files']} / 索引 {r['index_lines']} 行  操作 {len(r['ops'])} 个")
        for n in r["notes"]:
            print("        ·", n)
        for o in r["ops"]:
            tag = "写入" if o["op"] == "write" else "删除"
            print(f"        - {tag} {o['file']}：{o.get('reason') or o.get('description', '')[:40]}")
        for x in r["rejected"]:
            print("        ✗", x)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"compact-{args.stamp}.json"
    report_path.write_text(
        json.dumps({"stamp": args.stamp, "apply": args.apply, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告：{report_path}")


if __name__ == "__main__":
    asyncio.run(main())
