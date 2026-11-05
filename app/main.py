"""入口：装配依赖，挂 Web 渠道，起 Push 渠道（QQ 小号）。"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Windows 控制台默认 GBK，日志里有中文会乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .agent.factory import build_agent
from .alert import notify
from .approval import ApprovalGate
from .channel.qq_onebot import OneBotChannel
from .channel.web import WEB_CAPS, build_router
from .config import Config, load_config
from .credentials import inject_dsh_refs
from .deps import Deps
from .domain import Caps
from .memory import MemoryService
from .pump import pump_channel
from .router import Router
from .session import SessionService
from .store import Store
from .watchdog import Watchdog

log = logging.getLogger(__name__)


def _build_qq(cfg: Config) -> OneBotChannel | None:
    qq = cfg.channels.get("qq") or {}
    if not qq.get("enabled"):
        return None

    ws_url = qq.get("ws_url")
    if not ws_url:
        log.warning("qq.enabled=true 但没配 ws_url，跳过")
        return None

    token = qq.get("access_token") or ""
    if not token and qq.get("token_env"):
        token = os.getenv(str(qq["token_env"]), "")

    return OneBotChannel(
        ws_url=str(ws_url),
        access_token=token,
        self_id=str(qq["self_id"]) if qq.get("self_id") else None,
        group_requires_at=bool(qq.get("group_requires_at", True)),
        allowed_users=cfg.allowed_users,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,          # uvicorn 先配过 root handler，不强制重建就看不到应用日志
    )
    cfg = load_config()
    refs = inject_dsh_refs()          # 让 chat 的 OpenAI 兼容后端也能用 dsh 里的 key
    if refs:
        log.info("已注入 dsh 凭据：%s", sorted(refs))

    store = Store(cfg.db_path)
    store.init()

    agents = {name: build_agent(agent_cfg) for name, agent_cfg in cfg.agents.items()}
    memory = MemoryService(cfg) if cfg.memory.enabled else None
    sessions = SessionService(store, cfg, memory)
    caps: dict[str, Caps] = {"web": WEB_CAPS}
    gate = ApprovalGate()
    router = Router(cfg, store, sessions, agents, caps, gate)

    tasks: list[asyncio.Task] = []
    channels: dict[str, object] = {}
    qq = _build_qq(cfg)
    if qq is not None:
        caps[qq.name] = qq.caps
        channels[qq.name] = qq
        tasks.append(asyncio.create_task(pump_channel(qq, router), name=qq.name))
        log.info("QQ 小号渠道已启动：%s", qq.ws_url)

    router.attach_channels(channels)

    if channels and cfg.alerts.enabled:
        watchdog = Watchdog(
            channels,
            interval_s=cfg.alerts.interval_s,
            threshold=cfg.alerts.threshold,
            notifier=notify,
        )
        tasks.append(asyncio.create_task(watchdog.run(), name="watchdog"))

    app.state.deps = Deps(
        cfg=cfg, store=store, sessions=sessions, router=router, agents=agents, caps=caps
    )
    log.info("FishBot 就绪，后端：%s", {k: v.name for k, v in agents.items()})
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for agent in agents.values():
            shutdown = getattr(agent, "shutdown", None)
            if shutdown is not None:
                try:
                    await shutdown()
                except Exception:
                    log.debug("关闭后端失败", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(title="FishBot", version="0.2.0", lifespan=lifespan)
    app.include_router(build_router())

    @app.get("/healthz")
    async def healthz() -> dict:
        deps: Deps = app.state.deps
        return {
            "ok": True,
            "agents": {name: agent.name for name, agent in deps.agents.items()},
            "channels": sorted(deps.caps),
        }

    return app


app = create_app()
