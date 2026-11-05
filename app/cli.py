"""不开浏览器验证链路：python -m app.cli "你好\""""
from __future__ import annotations

import asyncio
import sys

from .agent.factory import build_agent
from .channel.web import WEB_CAPS
from .config import load_config
from .credentials import inject_dsh_refs
from .domain import Caps, InboundEvent
from .memory import MemoryService
from .router import Router
from .session import SessionService
from .store import Store


async def main(text: str) -> None:
    cfg = load_config()
    inject_dsh_refs()
    store = Store(cfg.db_path)
    store.init()
    agents = {name: build_agent(c) for name, c in cfg.agents.items()}
    memory = MemoryService(cfg) if cfg.memory.enabled else None
    sessions = SessionService(store, cfg, memory)
    # CLI 用非流式 caps：聚合后整段输出，便于肉眼检查人格与措辞
    caps: dict[str, Caps] = {"cli": Caps(max_len=4000, markdown=False), "web": WEB_CAPS}
    router = Router(cfg, store, sessions, agents, caps)

    ev = InboundEvent(channel="cli", conv_id="cli", user_id="owner", text=text, is_private=True)
    chunks: list[str] = []
    try:
        async for action in router.stream(ev):
            if action.delay:
                await asyncio.sleep(action.delay)
            chunks.append(action.text)
            print(f"[{action.kind} +{action.delay:.2f}s] {action.text}")
    finally:
        await sessions.flush()          # 等后台的记忆维护落库
        for agent in agents.values():
            shutdown = getattr(agent, "shutdown", None)
            if shutdown is not None:
                try:
                    await shutdown()
                except Exception:
                    pass

    if not chunks:
        print("（无输出：检查白名单或 agent 配置）")


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or "你好"
    asyncio.run(main(prompt))
