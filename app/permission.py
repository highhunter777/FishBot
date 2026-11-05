"""工具执行权限判定。

⚠️ 适用范围：这套分级只在**后端实现了 ACP `session/request_permission`** 时生效。

实测 dsh（DeepSeek Harness）不实现该请求，`new_session` 也不返回 modes，
所以对 dsh 而言这里全是死代码——真正的边界由它自己的沙盒策略
`DSH_PERMISSION_MODE`（read-only / workspace-write / danger-full-access）决定，
在 config.yaml 的 `worker.env` 里设置。

实现 permission 的后端（如 claude-agent-acp）会逐个工具调过来，此时按下面的规则判定。

分级原则（从宽到严）：
  只读工具            → ALLOW（静默放行，不打扰）
  写/执行/未知工具     → ASK（转到聊天里问 y/n）
  高危命令             → ASK（且文案里标明高危）
  非私聊 / 非本人      → DENY（工具权限根本不给）
  只读模式（read_only）→ 写操作直接 DENY
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

PERMISSION_TIMEOUT_S = 180.0   # 超时即拒绝


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(slots=True)
class Decision:
    verdict: Verdict
    reason: str = ""


HIGH_RISK = (
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[rRf]", re.I),
    re.compile(r"\bformat\b", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bshutdown\b|\breboot\b", re.I),
    re.compile(r"\bdd\s+if=", re.I),
    re.compile(r"git\s+push\s+.*(-f|--force)", re.I),
    re.compile(r"\bchmod\s+-R\s+777\b", re.I),
    re.compile(r":\(\)\s*\{.*\};:", re.S),          # fork bomb
    re.compile(r"(/etc/|/boot/|C:\\\\Windows)", re.I),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh", re.S | re.I),
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", re.I),
)

# 明确只读：可以直接放行
READONLY_TOOLS = (
    "read", "view", "list", "search", "grep", "find", "glob", "fetch", "think",
    "ls", "cat", "head", "tail", "stat", "pwd", "git status", "git diff", "git log",
)

# 写 / 执行：必须确认
WRITE_TOOLS = (
    "write", "edit", "create", "delete", "remove", "move", "rename", "patch",
    "shell", "bash", "exec", "terminal", "command", "run",
    "switch_mode", "apply", "install",
)


def _payload(tool: str, args: dict | str | None) -> str:
    if args is None:
        return tool
    if isinstance(args, str):
        return f"{tool} {args}"
    try:
        return f"{tool} {json.dumps(args, ensure_ascii=False)}"
    except (TypeError, ValueError):
        return f"{tool} {args}"


def evaluate(
    *,
    channel: str,
    is_private: bool,
    user_id: str,
    tool: str,
    args: dict | str | None = None,
    owner_ids: set[str] | None = None,
    read_only: bool = False,
) -> Decision:
    head = _payload(tool, args)
    low = tool.lower()

    # 1) 公开渠道（群聊/直播/评论）一律无工具权限
    if not is_private:
        return Decision(Verdict.DENY, "非私聊场景不授予工具权限")

    # 2) 非本人一律拒绝
    if owner_ids and user_id not in owner_ids:
        return Decision(Verdict.DENY, "操作者不在白名单")

    writes = any(w in low for w in WRITE_TOOLS)
    readonly = any(w in low for w in READONLY_TOOLS)

    # 3) 只读模式下不允许任何写操作
    if read_only and (writes or not readonly):
        return Decision(Verdict.DENY, "当前为只读模式")

    # 4) 高危命令：即使本人也要单独确认
    for pattern in HIGH_RISK:
        if pattern.search(head):
            return Decision(Verdict.ASK, "高危命令，需二次确认")

    # 5) 明确只读：静默放行
    if readonly and not writes:
        return Decision(Verdict.ALLOW, "只读工具")

    # 6) 其余（写/执行/未知）都要确认
    if writes:
        return Decision(Verdict.ASK, "写操作需确认")
    return Decision(Verdict.ASK, "未知工具，需确认")
