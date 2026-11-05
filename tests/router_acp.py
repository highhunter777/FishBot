"""Router + ACP + 审批闸门的端到端验证（用 mock agent，不碰真实文件）。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.acp_adapter import AcpAdapter  # noqa: E402
from app.agent.echo_adapter import EchoAdapter  # noqa: E402
from app.approval import ApprovalGate  # noqa: E402
from app.config import AgentCfg, Config, LimitsCfg  # noqa: E402
from app.domain import Caps, InboundEvent  # noqa: E402
from app.router import Router  # noqa: E402
from app.session import SessionService  # noqa: E402
from app.store import Store  # noqa: E402


async def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    store = Store(tmp / "t.db")
    store.init()

    cfg = Config(
        db_path=tmp / "t.db",
        channels={},
        agents={
            "chat": AgentCfg(name="chat", adapter="echo"),
            "worker": AgentCfg(
                name="worker",
                adapter="acp",
                command=[sys.executable, str(ROOT / "tests" / "mock_acp_agent.py")],
                cwd=str(ROOT),
            ),
        },
        routes=[{"default": True, "agent": "chat"}],
        limits={"*": LimitsCfg(interval_s=0.0, max_len=1500)},
        allowed_users=set(),
        prompts_dir=ROOT / "config" / "prompts",
        default_agent="chat",
    )

    sessions = SessionService(store, cfg)
    agents = {
        "chat": EchoAdapter(cfg.agents["chat"]),
        "worker": AcpAdapter(cfg.agents["worker"]),
    }
    gate = ApprovalGate()
    router = Router(cfg, store, sessions, agents, {"cli": Caps(max_len=1500, markdown=False)}, gate)

    actions: list = []

    async def consume():
        async for action in router.stream(ev_do):
            actions.append(action)

    ev_do = InboundEvent(channel="cli", conv_id="c1", user_id="owner", text="/do 危险操作：rm -rf build/", is_private=True)

    task = asyncio.create_task(consume())

    for _ in range(100):
        await asyncio.sleep(0.1)
        if gate.pending(ev_do.session_key):
            break
    else:
        raise AssertionError("没有等到审批请求")

    kinds = [a.kind for a in actions]
    print("审批前的动作：", kinds)
    assert "approval" in kinds, kinds
    approval = next(a for a in actions if a.kind == "approval")
    print("审批文案：", approval.text, approval.options)

    ev_yes = InboundEvent(channel="cli", conv_id="c1", user_id="owner", text="y", is_private=True)
    reply = [a.text async for a in router.stream(ev_yes)]
    print("用户回复后的响应：", reply)

    await asyncio.wait_for(task, timeout=30)

    text = "".join(a.text for a in actions)
    print("完整输出：", repr(text[:160]))
    assert "继续" in text, text     # 文案随人格变，只校验语义
    assert "selected" in text, text

    await agents["worker"].shutdown()
    print("\nRouter + ACP + 审批 通过")


if __name__ == "__main__":
    asyncio.run(main())
