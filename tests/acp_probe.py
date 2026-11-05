"""探测任意 ACP agent 命令能否握手：python tests/acp_probe.py <命令...>

例：
  python tests/acp_probe.py dsh --profile acp acp
  python tests/acp_probe.py cmd /c npx -y @zed-industries/codex-acp@latest
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.acp_adapter import AcpAdapter  # noqa: E402
from app.config import AgentCfg  # noqa: E402


async def main(command: list[str]) -> None:
    cfg = AgentCfg(name="probe", adapter="acp", command=command, cwd=str(ROOT / "workspace"), timeout_s=120)
    adapter = AcpAdapter(cfg)
    print("命令：", adapter.command)

    try:
        session_id = await adapter.open("probe", {})
    except Exception as exc:
        print(f"握手失败：{type(exc).__name__}: {exc}")
        print("排查：命令能单独跑起来吗？是否已登录/配 key？stdout 是否被日志污染？")
        return

    print("会话建立：", session_id)

    got = False
    async for event in adapter.prompt(session_id, "只回复两个字：接通"):
        if event.kind == "text_delta":
            got = True
            print(event.text, end="", flush=True)
        elif event.kind == "permission":
            print(f"\n[权限请求] {event.data} -> 允许")
            await adapter.approve(session_id, event.req_id or "", True)
        elif event.kind == "tool_call":
            print(f"\n[工具] {event.data}")
        elif event.kind == "error":
            print(f"\n[错误] {event.text}")
    print()
    await adapter.shutdown()
    print("结果：", "接通" if got else "没拿到文本输出")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1:]))
