"""把 Push 渠道（QQ/小黑盒/直播）的事件泵进 Router。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from .channel.base import PushChannel
from .router import Router

log = logging.getLogger(__name__)


async def pump_channel(channel: PushChannel, router: Router, concurrency: int = 3) -> None:
    sem = asyncio.Semaphore(concurrency)
    tasks: set[asyncio.Task] = set()

    async def handle(ev) -> None:
        async with sem:
            try:
                async for action in router.stream(ev):
                    if action.delay:
                        await asyncio.sleep(action.delay)
                    # 群里回复时带上 @，不然对方不知道在跟谁说话
                    if not ev.is_private and not action.at_user:
                        action = replace(action, at_user=ev.user_id)
                    await channel.send(ev.conv_id, action, ev.reply_to)
            except Exception:
                log.exception("处理消息失败：%s %s", ev.channel, ev.conv_id)

    try:
        async for ev in channel.run():
            task = asyncio.create_task(handle(ev))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
