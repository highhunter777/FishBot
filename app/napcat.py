"""NapCat 登录态辅助：识别是否卡在验证码/扫码，并给出 WebUI 地址。

只读 docker 日志和 WebUI 配置，不改容器。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys

from .config import ROOT

log = logging.getLogger(__name__)

MANUAL_HINTS = ("需要验证码", "密码回退需要验证码", "二维码已保存")


def webui_url() -> str:
    path = ROOT / "data" / "napcat" / "config" / "webui.json"
    port = 6099
    token = ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        port = int(data.get("port") or port)
        token = str(data.get("token") or "").strip()
    except Exception as exc:
        log.debug("读取 WebUI 配置失败：%s", exc)
    url = f"http://127.0.0.1:{port}/webui"
    return f"{url}?token={token}" if token else url


def login_required(container: str = "napcat", since_s: int = 1800) -> bool:
    """最近一段时间 NapCat 是否在等待人工验证码/扫码。"""
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", f"{since_s}s", container],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        log.debug("读取 NapCat 日志失败：%s", exc)
        return False
    text = f"{result.stdout}\n{result.stderr}"
    return any(hint in text for hint in MANUAL_HINTS)


def login_message(container: str = "napcat") -> str:
    return f"请打开 {webui_url()} 完成验证码/扫码登录，登录成功后会自动重连。"
