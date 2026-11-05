"""渠道健康巡检：掉线到阈值先自动重启 NapCat，仍不行再弹窗提醒人工。"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from typing import Callable

log = logging.getLogger(__name__)

Notifier = Callable[[str, str], bool]
Restarter = Callable[[str], bool]


def restart_container(name: str) -> bool:
    """后台重启容器，不阻塞事件循环；是否恢复交给下一轮巡检确认。"""
    try:
        subprocess.Popen(
            ["docker", "restart", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        log.warning("尝试自动重启容器：%s", name)
        return True
    except Exception as exc:
        log.warning("重启容器 %s 失败：%s", name, exc)
        return False


class Watchdog:
    def __init__(
        self,
        channels: dict[str, object],
        *,
        interval_s: float = 120.0,
        threshold: int = 3,
        notifier: Notifier | None = None,
        restarter: Restarter | None = None,
        restart_cooldown_s: float = 600.0,
    ) -> None:
        self.channels = channels
        self.interval = interval_s
        self.threshold = max(1, threshold)
        self.notifier = notifier or (lambda title, message: bool(log.warning("%s - %s", title, message)))
        self.restarter = restarter
        self.cooldown = restart_cooldown_s
        self._fails: dict[str, int] = {name: 0 for name in channels}
        self._alerted: dict[str, bool] = {name: False for name in channels}
        self._restarted: dict[str, bool] = {name: False for name in channels}
        self._last_restart: dict[str, float] = {}

    def _healthy(self, channel: object) -> bool:
        flag = getattr(channel, "connected", None)
        return True if flag is None else bool(flag)

    def _can_restart(self, name: str) -> bool:
        if self.restarter is None or self._restarted.get(name):
            return False
        last = self._last_restart.get(name, 0.0)
        return time.monotonic() - last >= self.cooldown

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
                if self._alerted.get(name) or self._restarted.get(name):
                    self.notifier("FishBot", f"{name} 已恢复连接")
                    self._alerted[name] = False
                    self._restarted[name] = False
                self._fails[name] = 0
                continue

            self._fails[name] = self._fails.get(name, 0) + 1
            minutes = int(self._fails[name] * self.interval // 60)

            if self._fails[name] >= self.threshold and self._can_restart(name):
                ok = bool(self.restarter(name))
                self._restarted[name] = True
                self._last_restart[name] = time.monotonic()
                self._fails[name] = 0        # 重启后给一轮阈值时间恢复
                message = f"{name} 已断开约 {minutes} 分钟，已自动重启 NapCat"
                if not ok:
                    message = f"{name} 已断开约 {minutes} 分钟，自动重启 NapCat 失败"
                self.notifier("FishBot 掉线处理", message)
                alerts.append(message)
                continue

            if self._fails[name] >= self.threshold and not self._alerted.get(name):
                message = f"{name} 已断开约 {minutes} 分钟，可能需要重新扫码登录"
                self.notifier("FishBot 掉线提醒", message)
                self._alerted[name] = True
                alerts.append(message)
        return alerts
