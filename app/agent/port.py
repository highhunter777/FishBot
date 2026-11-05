"""Agent 出口契约。换后端 = 新增一个实现 + 配置改一行。

约定：bridge 是 client，agent 是 server（ACP 场景为 stdio 子进程）。
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from ..domain import AgentCaps, AgentEvent


class AgentError(RuntimeError):
    pass


@runtime_checkable
class AgentPort(Protocol):
    name: str
    caps: AgentCaps

    async def open(self, session_key: str, profile: dict | None = None, meta: dict | None = None) -> str:
        """建立（或复用）会话，返回 session_id。

        meta 携带渠道上下文（channel / is_private / user_id / is_owner / scope），
        支持工具审批的后端用它做权限判定。
        """
        ...

    def prompt(
        self,
        session_id: str,
        text: str,
        *,
        messages: list[dict] | None = None,
        attachments: list | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """投递一轮输入。messages 为完整上下文（不支持自管上下文的后端用）。"""
        ...

    async def approve(self, session_id: str, req_id: str, allow: bool) -> None:
        ...

    async def cancel(self, session_id: str) -> None:
        ...

    async def close(self, session_id: str) -> None:
        ...
