"""未接上的后端占位：不阻塞启动，真正用到时才给出明确提示。"""
from __future__ import annotations

from ..domain import AgentCaps, AgentEvent


class UnavailableAgent:
    name = "unavailable"
    caps = AgentCaps(streaming=False, tools=False, permission=False, session_reuse=False)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def open(self, session_key: str, profile: dict | None = None, meta: dict | None = None) -> str:
        return session_key

    async def prompt(self, session_id: str, text: str, *, messages=None, attachments=None):
        yield AgentEvent("error", text=self.reason)

    async def approve(self, session_id: str, req_id: str, allow: bool) -> None:
        return None

    async def cancel(self, session_id: str) -> None:
        return None

    async def close(self, session_id: str) -> None:
        return None
