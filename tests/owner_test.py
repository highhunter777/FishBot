"""所有者分级测试：非主人只能用无工具的 chat。python tests/owner_test.py"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import LimitsCfg, load_config  # noqa: E402
from app.domain import AgentCaps, AgentEvent, Caps, InboundEvent  # noqa: E402
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


class FakeAgent:
    caps = AgentCaps(streaming=True, tools=True, session_reuse=True)

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.hits = 0
        self.last_messages: list[dict] = []

    async def open(self, key, profile=None, meta=None) -> str:
        return "s1"

    async def prompt(self, session_id, text, *, messages=None, attachments=None):
        self.hits += 1
        self.last_messages = messages or []
        yield AgentEvent("text_delta", text=f"<{self.tag}>")

    async def approve(self, session_id, req_id, allow) -> None:
        return None

    async def cancel(self, session_id) -> None:
        return None

    async def close(self, session_id) -> None:
        return None

    async def shutdown(self) -> None:
        return None


async def collect(router: Router, ev: InboundEvent) -> str:
    parts = [action.text async for action in router.stream(ev)]
    return "".join(parts)


async def main() -> None:
    cfg = load_config()
    cfg.allowed_users = {"owner1", "friend"}
    cfg.owner_users = {"owner1"}
    cfg.limits = {"*": LimitsCfg(interval_s=0, max_len=2000)}   # 关掉节流，避免测试互相干扰

    store = Store(pathlib.Path(tempfile.mkdtemp()) / "o.db")
    store.init()
    sessions = SessionService(store, cfg, None)
    chat, worker, lite = FakeAgent("chat"), FakeAgent("worker"), FakeAgent("lite")
    agents = {"chat": chat, "worker": worker, "worker_lite": lite}
    caps = {c: Caps(max_len=1500, markdown=False, stream=True) for c in ("qq_onebot", "cli", "web")}
    router = Router(cfg, store, sessions, agents, caps)

    print("非主人")
    ev = InboundEvent(channel="qq_onebot", conv_id="u:friend", user_id="friend", text="在吗", is_private=True)
    out = await collect(router, ev)
    check("路由到只读实例", lite.hits == 1 and worker.hits == 0, f"lite={lite.hits} worker={worker.hits}")
    check("有回复", "<lite>" in out, out[:60])
    system = " ".join(str(m.get("content", "")) for m in lite.last_messages)
    check("陌生人提示不是主人", "不是你的主人" in system, system[:60])
    check("禁止复述本机信息", "本机路径" in system, system[:60])

    ev = InboundEvent(channel="qq_onebot", conv_id="u:friend", user_id="friend", text="/do 删掉这些文件", is_private=True)
    out = await collect(router, ev)
    check("/do 被拒绝", "做不了" in out, out[:60])
    check("命令没进 agent", lite.hits == 1, str(lite.hits))

    ev = InboundEvent(channel="qq_onebot", conv_id="u:friend", user_id="friend", text="/help", is_private=True)
    out = await collect(router, ev)
    check("/help 也被拦", "做不了" in out, out[:60])

    print("主人")
    ev = InboundEvent(channel="qq_onebot", conv_id="u:owner1", user_id="owner1", text="在吗", is_private=True)
    await collect(router, ev)
    check("主人走可写实例", worker.hits == 1, str(worker.hits))
    owner_system = " ".join(str(m.get("content", "")) for m in worker.last_messages)
    check("主人没有额外限制", "不是你的主人" not in owner_system, owner_system[:40])

    ev = InboundEvent(channel="qq_onebot", conv_id="u:owner1", user_id="owner1", text="/do 看下磁盘", is_private=True)
    await collect(router, ev)
    check("/do 可用", worker.hits == 2, str(worker.hits))

    print("共用记忆")
    shared = store.history("qq_onebot", "u:owner1", scope="main")
    check("记忆写在 main 作用域", len(shared) >= 2, str(len(shared)))
    lite_profile = store.profile("owner1", "main")
    check("worker_lite 读同一份画像", isinstance(lite_profile, dict))

    print("本机渠道")
    ev = InboundEvent(channel="cli", conv_id="cli", user_id="stranger", text="/do 看下磁盘", is_private=True)
    out = await collect(router, ev)
    check("本机不受名单限制", worker.hits == 3 and "做不了" not in out, f"worker={worker.hits}")

    print("群聊")
    cfg.allowed_users = {"owner1"}
    cfg.owner_users = {"owner1"}
    before = lite.hits
    ev = InboundEvent(channel="qq_onebot", conv_id="g:1", user_id="stranger", text="你是谁", is_private=False)
    out = await collect(router, ev)
    check("群聊放行非白名单", lite.hits == before + 1, f"lite={lite.hits}")
    check("仍走只读实例", "<lite>" in out, out[:40])
    group_system = " ".join(str(m.get("content", "")) for m in lite.last_messages)
    check("群里也有防泄露约束", "不是你的主人" in group_system, group_system[:50])

    print("名单全空（谁都不是 owner）")
    cfg.allowed_users = set()
    cfg.owner_users = set()
    before = lite.hits
    ev = InboundEvent(channel="qq_onebot", conv_id="u:any", user_id="anyone", text="在吗", is_private=True)
    out = await collect(router, ev)
    check("陌生人能对话", "<lite>" in out, out[:40])
    ev = InboundEvent(channel="qq_onebot", conv_id="u:any", user_id="anyone", text="/do 删库", is_private=True)
    out = await collect(router, ev)
    check("但用不了命令", "做不了" in out and lite.hits == before + 1, f"lite={lite.hits} in={out[:30]}")

    print("\n全部通过：%d 项" % PASS)


if __name__ == "__main__":
    asyncio.run(main())
