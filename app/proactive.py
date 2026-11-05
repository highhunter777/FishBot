"""主动能力：定时提醒 + 启动时跟进被中断的任务。

提醒落 SQLite，进程重启后还在；到期由后台循环推回原会话。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from .domain import OutboundAction
from .store import Store

log = logging.getLogger(__name__)

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_REL = re.compile(
    r"^\s*(\d+|[一二三四五六七八九十两]+)\s*(秒|秒钟|s|分钟|分|m|小时|钟头|h|天|d)\s*"
    r"(?:后|以后|之后)?\s*(.*)$",
    re.I,
)
_ABS = re.compile(r"^\s*(\d{4}-\d{1,2}-\d{1,2})\s+(\d{1,2}):(\d{2})\s*(.*)$")
_CLOCK = re.compile(r"^\s*(今天|明天)?\s*(\d{1,2}):(\d{2})\s*(.*)$")

_UNIT_S = {
    "秒": 1, "秒钟": 1, "s": 1,
    "分钟": 60, "分": 60, "m": 60,
    "小时": 3600, "钟头": 3600, "h": 3600,
    "天": 86400, "d": 86400,
}


def _cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text:
        return None
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_NUM.get(text)


def parse_when(text: str, now: datetime | None = None) -> tuple[int, str] | None:
    """解析“10m 喝水 / 明天 8:00 起床 / 2026-11-06 08:00 开会”。"""
    base = now or datetime.now()
    raw = (text or "").strip()
    if not raw:
        return None

    match = _REL.match(raw)
    if match:
        amount = _cn_number(match.group(1))
        unit = match.group(2).lower()
        content = match.group(3).strip()
        if amount and content and unit in _UNIT_S:
            due = int(base.timestamp()) + amount * _UNIT_S[unit]
            return due, content

    match = _ABS.match(raw)
    if match:
        try:
            due_dt = datetime.strptime(f"{match.group(1)} {match.group(2)}:{match.group(3)}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        content = match.group(4).strip()
        if content and due_dt > base:
            return int(due_dt.timestamp()), content

    match = _CLOCK.match(raw)
    if match:
        day = match.group(1)
        try:
            due_dt = base.replace(hour=int(match.group(2)), minute=int(match.group(3)), second=0, microsecond=0)
        except ValueError:
            return None
        if day == "明天" or (not day and due_dt <= base):
            due_dt += timedelta(days=1)
        content = match.group(4).strip()
        if content and due_dt > base:
            return int(due_dt.timestamp()), content
    return None


def format_when(ts: int, now: datetime | None = None) -> str:
    base = now or datetime.now()
    target = datetime.fromtimestamp(ts)
    if target.date() == base.date():
        return target.strftime("今天 %H:%M")
    if target.date() == (base + timedelta(days=1)).date():
        return target.strftime("明天 %H:%M")
    return target.strftime("%m-%d %H:%M")


class Proactive:
    def __init__(self, store: Store, cfg) -> None:
        self.store = store
        self.cfg = cfg
        self.channels: dict[str, object] = {}

    def attach_channels(self, channels: dict[str, object]) -> None:
        self.channels = channels

    async def run(self) -> None:
        await asyncio.sleep(5)
        await self.recover_interrupted()
        while True:
            await asyncio.sleep(max(2.0, self.cfg.proactive.poll_s))
            try:
                await self.flush_due()
            except Exception:
                log.exception("主动提醒投递失败")

    async def flush_due(self) -> int:
        """把到期的提醒发回原会话；发送失败的下轮再试。"""
        sent = 0
        for item in self.store.due_reminders(limit=10):
            channel = self.channels.get(item["channel"])
            if channel is None:
                continue
            action = OutboundAction(
                "text",
                f"提醒：{item['content']}",
                at_user=item["user_id"] if str(item["conv_id"]).startswith("g:") else "",
            )
            try:
                await channel.send(item["conv_id"], action)
            except Exception as exc:
                log.warning("提醒发送失败（%s）：%s", item["id"], exc)
                continue
            self.store.mark_reminder(item["id"])
            sent += 1
            log.info("已发送提醒：%s %s", item["conv_id"], item["content"][:40])
        return sent

    async def recover_interrupted(self) -> int:
        """上次进程留下的 running 任务，主动告诉对方并结掉。"""
        lookback = int(self.cfg.proactive.interrupted_lookback_h * 3600)
        rows = self.store.interrupted_tasks(since=int(time.time()) - lookback)
        told = 0
        for row in rows:
            channel = self.channels.get(row["channel"])
            if channel is not None:
                stamp = datetime.fromtimestamp(row["started_at"]).strftime("%m-%d %H:%M")
                try:
                    await channel.send(
                        row["conv_id"],
                        OutboundAction(
                            "text",
                            f"上次有个任务在 {stamp} 被中断了（可能是重启或掉线）。"
                            "需要继续的话，把要做的事再发我一次就好。",
                        ),
                    )
                    told += 1
                except Exception as exc:
                    log.warning("中断任务通知失败（%s）：%s", row["id"], exc)
            self.store.task_finish(row["id"], "interrupted", "进程中断后未完成")
        if told:
            log.info("已跟进 %d 个中断任务", told)
        return told
