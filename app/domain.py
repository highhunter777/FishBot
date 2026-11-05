"""跨层数据契约。

所有渠道实现与 agent 实现只依赖本模块，互相不知道对方的存在。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal["text", "markdown", "image", "file", "voice", "card", "approval"]
AgentEventKind = Literal[
    "text_delta", "message", "progress", "tool_call", "tool_result",
    "permission", "done", "error",
]


@dataclass(frozen=True, slots=True)
class Caps:
    """渠道能力位。渲染层据此降级，业务代码不看渠道名。"""

    max_len: int
    markdown: bool = False
    buttons: bool = False
    stream: bool = False
    media: bool = False
    proactive: bool = False


@dataclass(slots=True)
class InboundEvent:
    channel: str
    conv_id: str          # 群号 / 私聊 uid / 房间号 / 页面会话
    user_id: str
    text: str
    is_private: bool = True
    attachments: list = field(default_factory=list)
    reply_to: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.conv_id}:{self.user_id}"


@dataclass(slots=True)
class OutboundAction:
    kind: ActionKind
    text: str = ""
    media: str | None = None
    options: list[str] | None = None
    delay: float = 0.0    # 发送前的人为停顿（秒），由节奏层给出
    at_user: str = ""     # 群里回复时 @ 谁（渠道决定要不要用）


@dataclass(frozen=True, slots=True)
class AgentCaps:
    streaming: bool = True
    tools: bool = False
    permission: bool = False
    session_reuse: bool = False
    resumable: bool = False          # 会话能跨进程恢复（ACP 的 session/resume）
    multimodal_in: bool = False
    max_input_chars: int = 20_000


@dataclass(slots=True)
class AgentEvent:
    kind: AgentEventKind
    text: str = ""
    data: dict | None = None
    req_id: str | None = None
