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
    # 同一会话严格串行：ACP 端同一 session 不允许多个 prompt 同时在飞
    locks: dict[str, asyncio.Lock] = {}

    async def handle(ev) -> None:
        lock = locks.setdefault(ev.conv_id, asyncio.Lock())
        async with lock:
            async with sem:
                try:
                    # 附件下载/转写在 handler 任务里做，不挡住同一个渠道的其他消息
                    prepare = getattr(channel, "prepare", None)
                    if prepare is not None:
                        ev = await prepare(ev)

                    first = True
                    async for action in router.stream(ev):
                        if action.delay:
                            await asyncio.sleep(action.delay)
                        # 群里只在第一条带上 @，分段回复不重复打扰
                        if not ev.is_private and first and not action.at_user:
                            action = replace(action, at_user=ev.user_id)
                        first = False
                        # 聊天不引用原消息：每条都带引用条太吵
                        await channel.send(ev.conv_id, action)
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
