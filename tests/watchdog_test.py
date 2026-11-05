"""掉线巡检测试：python tests/watchdog_test.py"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.watchdog import Watchdog  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


class FakeChannel:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


def main() -> None:
    print("掉线巡检")
    sent: list[str] = []

    def notifier(title: str, message: str) -> bool:
        sent.append(f"{title}|{message}")
        return True

    channel = FakeChannel(connected=False)
    dog = Watchdog({"qq_onebot": channel}, interval_s=120, threshold=3, notifier=notifier)

    dog.check_once()
    dog.check_once()
    check("未到阈值不告警", not sent, str(sent))

    dog.check_once()
    check("第 3 次触发告警", len(sent) == 1, str(sent))
    check("文案含分钟数", "6 分钟" in sent[0], sent[0])

    dog.check_once()
    dog.check_once()
    check("不重复刷告警", len(sent) == 1, str(sent))

    channel.connected = True
    dog.check_once()
    check("恢复后通知一次", len(sent) == 2 and "恢复" in sent[1], str(sent))

    dog.check_once()
    check("恢复后不再通知", len(sent) == 2, str(sent))

    # 缺 connected 属性的渠道视为健康
    class NoFlag:
        pass

    sent.clear()
    dog2 = Watchdog({"x": NoFlag()}, interval_s=120, threshold=1, notifier=notifier)
    dog2.check_once()
    check("无 connected 属性视为健康", not sent, str(sent))

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
