"""渠道契约。

- ChannelPort：所有渠道必须能发消息
- PushChannel：能主动收消息的渠道（QQ / 小黑盒 / 直播），实现 run()
- Web 这类请求驱动的渠道只实现 send()，由 HTTP 路由直接调 Router
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from ..domain import Caps, InboundEvent, OutboundAction


@runtime_checkable
class ChannelPort(Protocol):
    name: str
    caps: Caps

    async def send(
        self,
        conv_id: str,
        action: OutboundAction,
        reply_to: str | None = None,
    ) -> None:
        ...


@runtime_checkable
class PushChannel(ChannelPort, Protocol):
    def run(self) -> AsyncIterator[InboundEvent]:
        ...
