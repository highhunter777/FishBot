"""零依赖烟雾测试：python tests/smoke.py"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import persona, permission, render  # noqa: E402
from app.agent.echo_adapter import EchoAdapter  # noqa: E402
from app.config import AgentCfg, Config, LimitsCfg  # noqa: E402
from app.domain import Caps, InboundEvent, OutboundAction  # noqa: E402
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


def test_store() -> None:
    print("store")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    store = Store(tmp)
    store.init()
    store.add_message("cli", "c1", "u1", "user", "在吗")
    store.add_message("cli", "c1", "u1", "assistant", "在")
    check("history 顺序", [m["content"] for m in store.history("cli", "c1")] == ["在吗", "在"])
    check("count", store.count_messages("cli", "c1") == 2)

    for i in range(30):
        store.add_message("cli", "c1", "u1", "user", f"m{i}")
    dropped = store.drop_oldest("cli", "c1", keep=10)
    check("drop_oldest 保留 10", store.count_messages("cli", "c1") == 10)
    check("drop_oldest 返回被删", len(dropped) == 22)

    store.set_summary("cli", "c1", "聊过天")
    check("summary", store.get_summary("cli", "c1") == "聊过天")
    check("profile 初始空", store.profile("u1") == {})
    store.merge_profile("u1", {"称呼": "老王"})
    check("profile 写入", store.profile("u1")["称呼"] == "老王")

    store.clear("cli", "c1")
    check("clear", store.count_messages("cli", "c1") == 0 and store.get_summary("cli", "c1") == "")


def test_persona() -> None:
    print("persona")
    cleaned = persona.de_ai("首先，正文保留\n总之，就这样")
    check("去 AI 味", "首先" not in cleaned and "总之" not in cleaned and "正文保留" in cleaned, cleaned)
    check("去编号", not persona.de_ai("1. 第一步").startswith("1."))
    short = persona.split_like_human("在", max_len=1500)
    check("短答不切", short == ["在"])
    long = persona.split_like_human("段落一。" * 200, max_len=200, soft_len=100)
    check("长答切分且不超长", len(long) > 1 and all(len(x) <= 200 for x in long))
    check("延迟在区间内", 0.5 <= persona.typing_delay("你好啊") <= 5.0)
    check("快回答不 ack", persona.should_ack(0.2) is None)


def test_render() -> None:
    print("render")
    plain = Caps(max_len=1500, markdown=False)
    rich = Caps(max_len=4000, markdown=True)
    parts = render.render(OutboundAction("markdown", "**粗** `code`"), plain)
    check("markdown 降级为纯文本", "**" not in parts[0] and "粗" in parts[0], parts[0])
    parts = render.render(OutboundAction("markdown", "**粗**"), rich)
    check("markdown 渠道保留", "**粗**" in parts[0])
    parts = render.render(OutboundAction("approval", "要执行 rm", options=["允许"]), plain)
    check("按钮降级为 y/n", "回复 y" in parts[0], parts[0])
    check("超长分片", len(render.render(OutboundAction("text", "啊" * 100), Caps(max_len=30))) == 4)


def test_permission() -> None:
    print("permission")
    V = permission.Verdict
    check("群聊零权限", permission.evaluate(channel="qq", is_private=False, user_id="1",
                                        tool="shell", args={}, owner_ids={"1"}).verdict is V.DENY)
    check("非本人拒绝", permission.evaluate(channel="qq", is_private=True, user_id="2",
                                       tool="read", args={}, owner_ids={"1"}).verdict is V.DENY)
    check("高危要确认", permission.evaluate(channel="qq", is_private=True, user_id="1",
                                        tool="shell", args={"cmd": "rm -rf /"}, owner_ids={"1"}).verdict is V.ASK)
    check("写操作要确认", permission.evaluate(channel="qq", is_private=True, user_id="1",
                                         tool="write", args={"path": "a.txt"}, owner_ids={"1"}).verdict is V.ASK)
    check("只读直接放行", permission.evaluate(channel="qq", is_private=True, user_id="1",
                                         tool="read_file", args={"path": "a.txt"}, owner_ids={"1"}).verdict is V.ALLOW)


def build_cfg(db: pathlib.Path) -> Config:
    return Config(
        db_path=db,
        channels={"web": {"enabled": True}},
        agents={"chat": AgentCfg(name="chat", adapter="echo")},
        routes=[{"default": True, "agent": "chat"}],
        limits={"*": LimitsCfg(interval_s=0.0, max_len=1500)},
        allowed_users=set(),
        prompts_dir=ROOT / "config" / "prompts",
        default_agent="chat",
    )


async def test_router_end_to_end() -> None:
    print("router 端到端")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "e2e.db"
    store = Store(tmp)
    store.init()
    cfg = build_cfg(tmp)
    sessions = SessionService(store, cfg)
    agents = {"chat": EchoAdapter(cfg.agents["chat"])}
    router = Router(cfg, store, sessions, agents, {"cli": Caps(max_len=1500, markdown=False)})

    ev = InboundEvent(channel="cli", conv_id="c1", user_id="owner", text="在吗", is_private=True)
    actions = [a async for a in router.stream(ev)]
    joined = "".join(a.text for a in actions)
    check("有输出", bool(joined), joined[:80])
    check("回复可读", "链路是通的" in joined, joined[:80])
    check("写入两轮消息", store.count_messages("cli", "c1") == 2)

    ev2 = InboundEvent(channel="cli", conv_id="c1", user_id="owner", text="/new", is_private=True)
    out = "".join([a.text async for a in router.stream(ev2)])
    check("/new 清空", store.count_messages("cli", "c1") == 0 and "清" in out)

    ev3 = InboundEvent(channel="cli", conv_id="c1", user_id="owner", text="/remember 称呼=老王", is_private=True)
    await anext(router.stream(ev3))
    check("/remember 生效", store.profile("owner").get("称呼") == "老王")


async def main() -> None:
    test_store()
    test_persona()
    test_render()
    test_permission()
    await test_router_end_to_end()
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    asyncio.run(main())
