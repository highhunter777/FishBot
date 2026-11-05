"""复用 dsh 桌面端已保存的凭据。

桌面端把各家 key 存在 ~/.dsh/.credentials.yaml 的 refs 里（形如 ME_API_KEY: sk-...）。
这里只读、只注入进程环境，不落日志、不外发。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def load_dsh_refs() -> dict[str, str]:
    path = os.path.expanduser("~/.dsh/.credentials.yaml")
    if not os.path.exists(path):
        return {}
    try:
        import yaml

        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        refs = data.get("refs") or {}
        return {str(k): str(v) for k, v in refs.items() if v}
    except Exception as exc:
        log.warning("读取 dsh 凭据失败：%s", exc)
        return {}


def inject_dsh_refs() -> dict[str, str]:
    """把 refs 注入当前进程环境（不覆盖已有变量）。"""
    refs = load_dsh_refs()
    for key, value in refs.items():
        os.environ.setdefault(key, value)
    return refs
