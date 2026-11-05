"""用 config.yaml 里配的 worker 真跑一次 ACP agent：python tests/acp_real.py"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.acp_adapter import AcpAdapter  # noqa: E402
from app.config import load_config  # noqa: E402


async def main() -> None:
    cfg = load_config()
    agent_cfg = cfg.agents.get("worker")
    if agent_cfg is None:
        raise SystemExit("config.yaml 里没有 worker")
    print("启动命令：", agent_cfg.command)
    print("工作目录：", agent_cfg.cwd)

    adapter = AcpAdapter(agent_cfg)
    session_id = await adapter.open("real", {})
    print("会话：", session_id)

    async for event in adapter.prompt(session_id, "只回复两个字：接通"):
        if event.kind == "text_delta":
            print(event.text, end="", flush=True)
        elif event.kind == "permission":
            print(f"\n[权限请求] {event.data}")
            await adapter.approve(session_id, event.req_id or "", True)
        elif event.kind == "tool_call":
            print(f"\n[工具] {event.data}")
        elif event.kind == "error":
            print(f"\n[错误] {event.text}")
    print()
    await adapter.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
