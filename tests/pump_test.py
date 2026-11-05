"""验证 pump：附件预处理只做一次、群里只在第一条 @。"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.domain import InboundEvent, OutboundAction  # noqa: E402
from app.pump import pump_channel  # noqa: E402

PASS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if not ok:
        raise AssertionError(f"{name}：{detail}")
    PASS += 1
    print(f"  ok  {name}")


class FakeChannel:
    name = "qq_onebot"
    caps = None

    def __init__(self, event: InboundEvent) -> None:
        self.event = event
        self.sent: list[OutboundAction] = []
        self.prepared = 0
        self.done = asyncio.Event()

    async def prepare(self, event: InboundEvent) -> InboundEvent:
        self.prepared += 1
        return event

    async def run(self):
        yield self.event
        await self.done.wait()

    async def send(self, conv_id, action, reply_to=None) -> None:
        self.sent.append(action)
        if len(self.sent) >= 3:
            self.done.set()


class FakeRouter:
    async def stream(self, ev):
        for i in range(3):
            yield OutboundAction("text", f"第{i}段")


async def main() -> None:
    group = FakeChannel(
        InboundEvent(channel="qq_onebot", conv_id="g:555", user_id="12345", text="问", is_private=False)
    )
    await asyncio.wait_for(pump_channel(group, FakeRouter()), timeout=3)
    check("附件预处理执行一次", group.prepared == 1, str(group.prepared))
    check("三段都发出", len(group.sent) == 3, str(len(group.sent)))
    check("群里只有第一条带 @", group.sent[0].at_user == "12345", str(group.sent[0].at_user))
    check(
        "后续分段不再 @",
        all(not a.at_user for a in group.sent[1:]),
        str([a.at_user for a in group.sent]),
    )

    private = FakeChannel(
        InboundEvent(channel="qq_onebot", conv_id="u:12345", user_id="12345", text="问", is_private=True)
    )
    await asyncio.wait_for(pump_channel(private, FakeRouter()), timeout=3)
    check("私聊不 @", all(not a.at_user for a in private.sent), str(len(private.sent)))

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    asyncio.run(main())
