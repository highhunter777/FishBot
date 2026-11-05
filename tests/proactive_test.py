"""主动能力测试：提醒解析、到期投递、中断任务跟进。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.echo_adapter import EchoAdapter  # noqa: E402
from app.config import load_config  # noqa: E402
from app.domain import InboundEvent  # noqa: E402
from app.proactive import Proactive, format_when, parse_when  # noqa: E402
from app.router import Router  # noqa: E402
from app.session import SessionService  # noqa: E402
from app.store import Store  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send(self, conv_id, action, reply_to=None) -> None:
        self.sent.append((conv_id, action))


class OfflineChannel(FakeChannel):
    async def send(self, conv_id, action, reply_to=None) -> None:
        raise RuntimeError("offline")


def test_parse() -> None:
    print("时间解析")
    now = datetime(2026, 11, 5, 12, 0, 0)
    due, content = parse_when("10m 喝水", now)
    check("相对分钟", due == int(now.timestamp()) + 600 and content == "喝水", str((due, content)))

    due, content = parse_when("两小时 收衣服", now)
    check("中文数字", content == "收衣服" and due == int(now.timestamp()) + 7200, str(due))

    due, content = parse_when("明天 8:00 起床", now)
    check("明天时刻", datetime.fromtimestamp(due).strftime("%m-%d %H:%M") == "11-06 08:00", str(content))

    due, content = parse_when("2026-11-06 08:00 开会", now)
    check("绝对时间", content == "开会" and datetime.fromtimestamp(due).hour == 8, str(due))

    check("坏格式", parse_when("随便说说", now) is None)
    check("过去时间", parse_when("2020-01-01 08:00 过期", now) is None)


async def test_reminder() -> None:
    print("提醒投递")
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "p.db")
    store.init()
    cfg = load_config()
    channel = FakeChannel()
    svc = Proactive(store, cfg)
    svc.attach_channels({"qq_onebot": channel})

    rid = store.add_reminder("qq_onebot", "u:1", "1", "喝水", int(time.time()) - 1)
    assert await svc.flush_due() == 1
    check("到期提醒已发送", len(channel.sent) == 1, str(channel.sent))
    check("提醒文案", channel.sent[0][1].text.startswith("提醒：喝水"), channel.sent[0][1].text)
    check("状态已标记", store.reminder_status(rid) == "sent", str(store.reminder_status(rid)))

    future = store.add_reminder("qq_onebot", "u:1", "1", "明天的事", int(time.time()) + 3600)
    check("未到期不发送", await svc.flush_due() == 0)
    check("未到期仍 pending", store.reminder_status(future) == "pending")

    off = OfflineChannel()
    svc.attach_channels({"qq_onebot": off})
    store.add_reminder("qq_onebot", "u:1", "1", "离线时的提醒", int(time.time()) - 1)
    check("离线不发也留 pending", await svc.flush_due() == 0)
    rows = store.pending_reminders("qq_onebot", "u:1")
    check("失败保留待重试", any("离线时" in r["content"] for r in rows), str(rows))


async def test_interrupted() -> None:
    print("中断任务跟进")
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    store.init()
    cfg = load_config()
    channel = FakeChannel()
    svc = Proactive(store, cfg)
    svc.attach_channels({"qq_onebot": channel})

    task_id = store.task_start("qq_onebot", "u:1")
    assert await svc.recover_interrupted() == 1
    check("通知了中断任务", len(channel.sent) == 1, str(channel.sent))
    check("文案含中断", "中断" in channel.sent[0][1].text, channel.sent[0][1].text)
    check("任务已结掉", store.interrupted_tasks() == [], str(store.interrupted_tasks()))
    _ = task_id


async def test_command() -> None:
    print("/remind 指令")
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "c.db")
    store.init()
    cfg = load_config()
    cfg.allowed_users = {"2090127048"}
    cfg.owner_users = {"2090127048"}
    sessions = SessionService(store, cfg)
    router = Router(cfg, store, sessions, {"chat": EchoAdapter(cfg.agents["chat"])}, {})
    svc = Proactive(store, cfg)
    router.proactive = svc

    ev = InboundEvent(
        channel="qq_onebot",
        conv_id="u:2090127048",
        user_id="2090127048",
        text="/remind 10m 喝水",
        is_private=True,
    )
    actions = [a async for a in router.stream(ev)]
    check("回复了确认", bool(actions) and "提醒你" in actions[0].text, str(actions))
    check("提醒已入库", bool(store.pending_reminders("qq_onebot", "u:2090127048")))
    _ = format_when


def main() -> None:
    test_parse()
    asyncio.run(test_reminder())
    asyncio.run(test_interrupted())
    asyncio.run(test_command())
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
