"""渠道健康巡检：掉线一段时间没人处理，就弹个桌面通知。"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger(__name__)

Notifier = Callable[[str, str], bool]


class Watchdog:
    def __init__(
        self,
        channels: dict[str, object],
        *,
        interval_s: float = 120.0,
        threshold: int = 3,
        notifier: Notifier | None = None,
    ) -> None:
        self.channels = channels
        self.interval = interval_s
        self.threshold = max(1, threshold)
        self.notifier = notifier or (lambda title, message: bool(log.warning("%s - %s", title, message)))
        self._fails: dict[str, int] = {name: 0 for name in channels}
        self._alerted: dict[str, bool] = {name: False for name in channels}

    def _healthy(self, channel: object) -> bool:
        flag = getattr(channel, "connected", None)
        return True if flag is None else bool(flag)

    async def run(self) -> None:
        log.info("巡检已启动：%s，阈值 %d 次 × %.0fs", list(self.channels), self.threshold, self.interval)
        while True:
            await asyncio.sleep(self.interval)
            try:
                self.check_once()
            except Exception:
                log.exception("巡检异常")

    def check_once(self) -> list[str]:
        alerts: list[str] = []
        for name, channel in self.channels.items():
            if self._healthy(channel):
                if self._alerted.get(name):
                    self.notifier("FishBot", f"{name} 已恢复连接")
                    self._alerted[name] = False
                self._fails[name] = 0
                continue

            self._fails[name] = self._fails.get(name, 0) + 1
            if self._fails[name] >= self.threshold and not self._alerted.get(name):
                minutes = int(self._fails[name] * self.interval // 60)
                message = f"{name} 已断开约 {minutes} 分钟，可能要重新扫码登录"
                self.notifier("FishBot 掉线提醒", message)
                self._alerted[name] = True
                alerts.append(message)
        return alerts
