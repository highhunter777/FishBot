"""媒体文件清理测试：python tests/media_cleanup_test.py"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.media_cleanup import prune_media_files  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def main() -> None:
    base = pathlib.Path(tempfile.mkdtemp())
    old_dir = base / "inbox" / "2026-01-01"
    old_dir.mkdir(parents=True)
    old = old_dir / "old.jpg"
    old.write_bytes(b"old")
    stamp = time.time() - 10 * 86400
    os.utime(old, (stamp, stamp))

    fresh_dir = base / "inbox" / "2026-11-05"
    fresh_dir.mkdir(parents=True)
    fresh = fresh_dir / "fresh.jpg"
    fresh.write_bytes(b"fresh")

    removed = prune_media_files(base, days=7)
    check("删掉过期文件", removed == 1, str(removed))
    check("过期文件不存在", not old.exists())
    check("新文件保留", fresh.exists())
    check("空掉的旧目录被收掉", not old_dir.exists())
    check("新目录还在", fresh_dir.exists())
    check("inbox 根不动", (base / "inbox").is_dir())
    check("没有 media_root 时安全返回", prune_media_files("", 7) == 0)

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
