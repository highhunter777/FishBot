"""极简 OneBot 11 WebSocket 服务端，用于验证 QQ 渠道（不需要真 NapCat）。"""
from __future__ import annotations

import asyncio
import json


class MockOneBot:
    def __init__(self, port: int = 30011) -> None:
        self.port = port
        self.received: list[dict] = []
        self.connected = asyncio.Event()
        self._ws = None
        self._server = None

    async def handler(self, ws) -> None:
        self._ws = ws
        self.connected.set()
        try:
            async for raw in ws:
                request = json.loads(raw)
                self.received.append(request)
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"user_id": 99999, "message_id": 100, "nickname": "小号"},
                            "echo": request.get("echo"),
                        },
                        ensure_ascii=False,
                    )
                )
        except Exception:
            pass

    async def send_event(self, payload: dict) -> None:
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def __aenter__(self) -> "MockOneBot":
        self._server = await websockets_serve(self.handler, "127.0.0.1", self.port)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def websockets_serve(handler, host, port):
    import websockets

    return await websockets.serve(handler, host, port)
