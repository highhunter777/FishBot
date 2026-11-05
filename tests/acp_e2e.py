"""验证 AcpAdapter <-> ACP agent 的完整往返：python tests/acp_e2e.py"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.acp_adapter import AcpAdapter  # noqa: E402
from app.config import AgentCfg  # noqa: E402


async def run() -> None:
    cfg = AgentCfg(
        name="worker",
        adapter="acp",
        command=[sys.executable, str(ROOT / "tests" / "mock_acp_agent.py")],
        cwd=str(ROOT),
        timeout_s=60,
    )
    adapter = AcpAdapter(cfg)
    # 必须带渠道上下文，否则权限层会按"非私聊"直接拒绝
    meta = {"channel": "cli", "is_private": True, "user_id": "owner", "is_owner": True, "scope": "worker"}
    session_id = await adapter.open("test", {}, meta)
    print("session:", session_id)

    text = ""
    async for event in adapter.prompt(session_id, "你好"):
        if event.kind == "text_delta":
            text += event.text
    print("普通回复:", repr(text))
    assert "mock agent" in text, text

    # 第二轮：触发权限请求，模拟用户点"允许"
    text2 = ""
    seen_permission = False
    async for event in adapter.prompt(session_id, "帮我危险操作：rm -rf build/"):
        if event.kind == "text_delta":
            text2 += event.text
        elif event.kind == "permission":
            seen_permission = True
            print("收到权限请求:", event.data, "req_id=", event.req_id)
            await adapter.approve(session_id, event.req_id or "", True)
    print("权限轮回复:", repr(text2))
    assert seen_permission, "没有收到 permission 事件"
    assert "selected" in text2, text2

    await adapter.shutdown()
    print("\nACP 端到端通过")


if __name__ == "__main__":
    asyncio.run(run())
