"""审批闸门：agent 请求权限时挂起，等用户在聊天里回 y/n。

默认拒绝：超时、非本人、无法解析，全部 deny。
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

YES = {"y", "yes", "好", "可以", "允许", "同意", "行", "是", "1", "嗯"}
NO = {"n", "no", "不", "不行", "拒绝", "取消", "否", "0", "算了"}


def parse_answer(text: str) -> bool | None:
    token = (text or "").strip().lower()
    if token in YES:
        return True
    if token in NO:
        return False
    return None


class ApprovalGate:
    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future] = {}

    def pending(self, key: str) -> bool:
        future = self._waiters.get(key)
        return future is not None and not future.done()

    async def wait(self, key: str, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._waiters[key] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            log.info("审批超时，默认拒绝：%s", key)
            return False
        finally:
            self._waiters.pop(key, None)

    def resolve(self, key: str, allow: bool) -> bool:
        future = self._waiters.get(key)
        if future is None or future.done():
            return False
        future.set_result(allow)
        return True
