"""本地演示后端：不联网，用于第一步跑通链路。

不是聊天机器人，只是让管路可见。配好 LLM_KEY 后会自动切到 openai 后端。
"""
from __future__ import annotations

import asyncio
import random

from ..config import AgentCfg
from ..domain import AgentCaps, AgentEvent


class EchoAdapter:
    name = "echo"
    caps = AgentCaps(streaming=True, tools=False, permission=False, session_reuse=True)

    def __init__(self, cfg: AgentCfg, reason: str = "手动指定") -> None:
        self.cfg = cfg
        self.reason = reason

    async def open(self, session_key: str, profile: dict | None = None, meta: dict | None = None) -> str:
        return session_key

    async def prompt(
        self,
        session_id: str,
        text: str,
        *,
        messages: list[dict] | None = None,
        attachments: list | None = None,
    ):
        turns = sum(1 for m in (messages or []) if m.get("role") == "user")
        reply = (
            f"链路是通的（{self.reason}）。收到「{text[:40]}」，这是第 {turns} 轮对话。\n"
            "配好 LLM_KEY 之后这里会换成真实回复。"
        )
        for ch in reply:
            await asyncio.sleep(random.uniform(0.005, 0.02))
            yield AgentEvent("text_delta", text=ch)
        yield AgentEvent("done", data={"adapter": "echo"})

    async def approve(self, session_id: str, req_id: str, allow: bool) -> None:
        return None

    async def cancel(self, session_id: str) -> None:
        return None

    async def close(self, session_id: str) -> None:
        return None
