"""核心数据备份：数据库、记忆库、协议端与框架配置。

设计要点
- SQLite 走在线 backup API，而不是直接拷文件。data_v4.db 处于 WAL 模式，
  直接 copy 可能拿到未合并的半截快照；backup() 由 SQLite 自己保证一致性。
- bot-memory 是 Markdown 文本，整棵复制（很小，约 200KB）。
- plugins 是自建插件的源码，删了就得重写，必须进包（但排除 __pycache__ 等缓存）。
- 配置只挑真正需要复原的那几个，不铺整个 data 目录。
- 输出单个 zip，按时间戳命名，只保留最近 KEEP 份。

用法
    python backup_core.py            # 备份一次
    python backup_core.py --tag pre-edit   # 带标签，便于识别改动前快照
    python backup_core.py --list     # 只看现有备份
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\qqbot")
BACKUP_DIR = BASE / "backups"
KEEP = 14

SQLITE_SOURCES = [
    BASE / "astrbot" / "data" / "data_v4.db",
]

FILE_SOURCES = [
    BASE / "astrbot" / "data" / "cmd_config.json",
    BASE / "astrbot" / "data" / "mcp_server.json",
]

DIR_SOURCES = [
    BASE / "astrbot" / "data" / "config",
    BASE / "astrbot" / "data" / "plugins",
    BASE / "bot-memory",
    BASE / "snowluma" / "config",
]

# 任意层级出现这些目录名就整枝跳过（缓存、子备份都不必进包）
EXCLUDE_DIR_NAMES = {"_backup", "_archive", "__pycache__", "node_modules"}


def snapshot_sqlite(src: Path, dst: Path) -> None:
    """用 SQLite 在线备份 API 做一致性快照。"""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def rotate() -> list[str]:
    old = sorted(BACKUP_DIR.glob("core-*.zip"))
    removed = []
    for path in old[:-KEEP] if len(old) > KEEP else []:
        path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def do_backup(tag: str = "") -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{tag}" if tag else ""
    out = BACKUP_DIR / f"core-{stamp}{suffix}.zip"

    with tempfile.TemporaryDirectory(prefix="qqbot-bk-") as tmp:
        tmp_dir = Path(tmp)
        staged: list[tuple[Path, str]] = []

        for src in SQLITE_SOURCES:
            if not src.exists():
                print(f"  [跳过] 不存在 {src}")
                continue
            dst = tmp_dir / f"{src.stem}.db"
            snapshot_sqlite(src, dst)
            staged.append((dst, f"db/{src.name}"))
            print(f"  [快照] {src.name} -> {dst.stat().st_size / 1024 / 1024:.2f} MB")

        for src in FILE_SOURCES:
            if src.exists():
                staged.append((src, f"config/{src.relative_to(BASE)}"))
            else:
                print(f"  [跳过] 不存在 {src}")

        for src_dir in DIR_SOURCES:
            if not src_dir.exists():
                print(f"  [跳过] 不存在 {src_dir}")
                continue
            count = 0
            for f in src_dir.rglob("*"):
                if not f.is_file():
                    continue
                # 记忆维护自己的备份/归档不再套娃；缓存（__pycache__/.pyc）一律不进包
                rel = f.relative_to(src_dir)
                if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
                    continue
                if f.suffix in {".db-wal", ".db-shm", ".pyc"}:
                    continue
                staged.append((f, f"data/{src_dir.name}/{rel.as_posix()}"))
                count += 1
            print(f"  [收集] {src_dir.name}/ {count} 个文件")

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for src, arc in staged:
                zf.write(src, arcname=arc.replace(chr(92), "/"))

    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        entries = len(zf.namelist())
    if bad is not None:
        print(f"!! 校验失败: {bad}", file=sys.stderr)
        return 1

    size = out.stat().st_size / 1024 / 1024
    print(f"\n备份完成: {out}")
    print(f"  {entries} 个条目 / {size:.2f} MB / 校验通过")

    removed = rotate()
    if removed:
        print(f"  轮转清理: {len(removed)} 份旧备份（保留最近 {KEEP} 份）")
    remaining = len(list(BACKUP_DIR.glob('core-*.zip')))
    print(f"  现有备份: {remaining} 份")
    return 0


def do_list() -> int:
    if not BACKUP_DIR.exists():
        print("还没有任何备份")
        return 0
    items = sorted(BACKUP_DIR.glob("core-*.zip"))
    if not items:
        print("还没有任何备份")
        return 0
    for p in items:
        ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {p.name:44s} {p.stat().st_size / 1024 / 1024:6.2f} MB  {ts}")
    print(f"\n共 {len(items)} 份，保留上限 {KEEP} 份")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="核心数据备份")
    parser.add_argument("--tag", default="", help="备份标签，例如 pre-edit")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    args = parser.parse_args()

    if args.list:
        return do_list()

    print("开始备份核心数据...")
    return do_backup(args.tag)


if __name__ == "__main__":
    sys.exit(main())
