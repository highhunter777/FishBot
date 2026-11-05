"""桌面通知（Windows 气泡）。失败就退回日志，不影响主流程。"""
from __future__ import annotations

import logging
import pathlib
import subprocess
import sys

log = logging.getLogger(__name__)

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "notify.ps1"


def notify(title: str, message: str) -> bool:
    if not SCRIPT.exists():
        log.warning("通知：%s - %s（脚本缺失）", title, message)
        return False

    if sys.platform != "win32":
        log.warning("通知：%s - %s", title, message)
        return False

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-Title",
                title,
                "-Message",
                message,
            ],
            capture_output=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("已发送桌面通知：%s - %s", title, message)
        return True
    except Exception as exc:
        log.warning("通知失败：%s", exc)
        return False
