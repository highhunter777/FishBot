"""Web 渠道：请求驱动，不走 run() 循环，直接喂给 Router。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..domain import Caps, InboundEvent
from .web_ui import PAGE

log = logging.getLogger(__name__)

WEB_CAPS = Caps(max_len=8000, markdown=True, buttons=True, stream=True, media=True, proactive=True)


class ChatIn(BaseModel):
    session_id: str = "web"
    user_id: str = "local"
    text: str


class ResetIn(BaseModel):
    session_id: str = "web"
    user_id: str = "local"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(PAGE)

    @router.post("/api/chat")
    async def chat(payload: ChatIn, request: Request):
        deps = request.app.state.deps
        ev = InboundEvent(
            channel="web",
            conv_id=payload.session_id,
            user_id=payload.user_id,
            text=payload.text,
            is_private=True,
        )

        async def event_stream():
            yield _sse({"type": "start"})
            try:
                async for action in deps.router.stream(ev):
                    yield _sse(
                        {
                            "type": "action",
                            "kind": action.kind,
                            "text": action.text,
                            "delay": round(action.delay, 3),
                        }
                    )
            except Exception:
                log.exception("web 流式响应失败")
                yield _sse({"type": "action", "kind": "text", "text": "这边出了点问题。", "delay": 0})
            yield _sse({"type": "end"})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/history")
    async def history(session_id: str, request: Request) -> dict:
        deps = request.app.state.deps
        return {"messages": deps.store.history("web", session_id, limit=40)}

    @router.post("/api/reset")
    async def reset(payload: ResetIn, request: Request) -> dict:
        deps = request.app.state.deps
        deps.store.clear("web", payload.session_id)
        return {"ok": True}

    return router
