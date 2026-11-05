"""装配好的运行时依赖，挂在 app.state.deps 上。"""
from __future__ import annotations

from dataclasses import dataclass

from .agent.port import AgentPort
from .config import Config
from .domain import Caps
from .router import Router
from .session import SessionService
from .store import Store


@dataclass(slots=True)
class Deps:
    cfg: Config
    store: Store
    sessions: SessionService
    router: Router
    agents: dict[str, AgentPort]
    caps: dict[str, Caps]
