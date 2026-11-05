"""最小 ACP agent，用于本地验证 bridge（不需要装 claude/gemini）。

python tests/mock_acp_agent.py   # 由 AcpAdapter 以子进程方式拉起
"""
from __future__ import annotations

import asyncio
import sys

from acp import run_agent
from acp.meta import PROTOCOL_VERSION
from acp.schema import (
    AgentMessageChunk,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallStart,
    ToolCallUpdate,
)


def _log(message: str) -> None:
    import pathlib
    path = pathlib.Path(__file__).with_name("mock_acp.log")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def _chunk(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(
        content=TextContentBlock(type="text", text=text),
        session_update="agent_message_chunk",
    )


class MockAgent:
    def __init__(self) -> None:
        self.conn = None
        self.sessions: dict[str, int] = {}

    def on_connect(self, conn) -> None:      # SDK 是同步回调，不能写成 async
        self.conn = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs):
        return InitializeResponse(protocol_version=PROTOCOL_VERSION)

    async def new_session(self, cwd: str, additional_directories=None, mcp_servers=None, **kwargs):
        session_id = f"mock-{len(self.sessions) + 1}"
        self.sessions[session_id] = 0
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, session_id: str, prompt, **kwargs):
        try:
            return await self._prompt(session_id, prompt)
        except Exception as exc:
            import traceback
            _log("prompt 失败：" + traceback.format_exc())
            raise

    async def _prompt(self, session_id: str, prompt):
        text = "".join(getattr(block, "text", "") for block in prompt)
        _log(f"prompt session={session_id} text={text!r}")
        for piece in ("收到：", text[:24], "\n这是 mock agent。", "不会真的动你的文件。"):
            await self.conn.session_update(session_id, _chunk(piece))
            await asyncio.sleep(0.05)

        if "危险" in text or "rm " in text:
            title = "执行 rm -rf build/"
            await self.conn.session_update(
                session_id,
                ToolCallStart(
                    tool_call_id="t1",
                    title=title,
                    kind="execute",
                    status="pending",
                    session_update="tool_call",
                ),
            )
            resp = await self.conn.request_permission(
                session_id,
                ToolCallUpdate(tool_call_id="t1", title=title, kind="execute"),
                [
                    PermissionOption(option_id="allow_once", name="允许一次", kind="allow_once"),
                    PermissionOption(option_id="reject_once", name="拒绝", kind="reject_once"),
                ],
            )
            outcome = getattr(resp.outcome, "outcome", "?")
            await self.conn.session_update(session_id, _chunk(f"\n审批结果：{outcome}"))

        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs) -> None:
        return None


if __name__ == "__main__":
    sys.stderr.write("mock acp agent started\n")
    sys.stderr.flush()
    asyncio.run(run_agent(MockAgent()))
