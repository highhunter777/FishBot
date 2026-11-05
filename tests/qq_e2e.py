"""验证 OneBot 渠道：事件解析、@ 过滤、去重、发送结构。"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mock_onebot import MockOneBot  # noqa: E402

from app.channel.qq_onebot import OneBotChannel  # noqa: E402
from app.domain import OutboundAction  # noqa: E402

PORT = 30011


async def main() -> None:
    async with MockOneBot(PORT) as server:
        channel = OneBotChannel(
            ws_url=f"ws://127.0.0.1:{PORT}",
            allowed_users={"12345"},
        )

        events: asyncio.Queue = asyncio.Queue()

        async def consume():
            async for event in channel.run():
                await events.put(event)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(server.connected.wait(), timeout=5)
        await asyncio.sleep(0.3)

        # 1) 私聊
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 12345,
                "message_id": 1,
                "message": [{"type": "text", "data": {"text": "在吗"}}],
                "raw_message": "在吗",
            }
        )
        ev = await asyncio.wait_for(events.get(), timeout=3)
        assert ev.is_private and ev.text == "在吗" and ev.conv_id == "u:12345", ev
        assert ev.reply_to == "1"
        print("ok 私聊解析")

        # 2) 重复消息应被丢弃
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 12345,
                "message_id": 1,
                "message": [{"type": "text", "data": {"text": "在吗"}}],
            }
        )
        try:
            await asyncio.wait_for(events.get(), timeout=1)
            raise AssertionError("重复消息没有被丢弃")
        except TimeoutError:
            print("ok 重复消息丢弃")

        # 3) 群里没 @ 应被丢弃
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 555,
                "user_id": 12345,
                "message_id": 3,
                "message": [{"type": "text", "data": {"text": "路过"}}],
            }
        )
        try:
            await asyncio.wait_for(events.get(), timeout=1)
            raise AssertionError("未 @ 的群消息不该进来")
        except TimeoutError:
            print("ok 群里未 @ 丢弃")

        # 4) 群里 @ 了小号
        await server.send_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 555,
                "user_id": 12345,
                "message_id": 4,
                "message": [
                    {"type": "at", "data": {"qq": "99999"}},
                    {"type": "text", "data": {"text": " 帮我看下"}},
                ],
            }
        )
        ev = await asyncio.wait_for(events.get(), timeout=3)
        assert not ev.is_private and ev.text == "帮我看下" and ev.conv_id == "g:555", ev
        print("ok 群 @ 解析")

        # 5) 陌生人（非白名单）在群里不该进来 —— 白名单非空时由 Router 拦，这里只验证 conv 正确
        # 6) 发送结构
        await channel.send("u:12345", OutboundAction("text", "收到[ CQ:at,qq=1 ]"))
        await asyncio.sleep(0.2)
        sent = [r for r in server.received if r.get("action") == "send_private_msg"]
        assert sent, server.received
        assert sent[-1]["params"]["message"] == [{"type": "text", "data": {"text": "收到[ CQ:at,qq=1 ]"}}]
        assert sent[-1]["params"]["user_id"] == 12345
        print("ok 私聊发送结构（消息段数组，天然免疫 CQ 注入）")

        await channel.send("g:555", OutboundAction("text", "群回复"))
        await asyncio.sleep(0.2)
        assert any(r.get("action") == "send_group_msg" for r in server.received)
        print("ok 群发送")

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    print("\nQQ OneBot 渠道通过")


if __name__ == "__main__":
    asyncio.run(main())
