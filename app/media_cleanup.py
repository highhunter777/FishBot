"""清理 inbox 里过期的媒体文件。

数据库里的 media 行由 `Store.media_prune()` 按 TTL 清掉；本模块补磁盘这一半：
删掉 `inbox/` 下 mtime 超过保留期的文件，再收掉空目录。
启动时跑一次，和 media_prune 成对。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def prune_media_files(media_root: str | Path, days: float = 7.0) -> int:
    """删除 <media_root>/inbox 下超过 days 天的文件；返回删除数量。"""
    if not media_root:
        return 0
    box = Path(media_root) / "inbox"
    if not box.is_dir():
        return 0

    cutoff = time.time() - max(days, 0) * 86400
    removed = 0
    for path in box.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            log.debug("清理媒体失败 %s：%s", path, exc)

    # 先删深层的空目录，避免父目录被非空子目录挡住
    folders = sorted((p for p in box.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed
