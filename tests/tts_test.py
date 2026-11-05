"""语音回复指令测试：/voice -> voice action（不真调 edge-tts）。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.echo_adapter import EchoAdapter  # noqa: E402
from app.config import load_config  # noqa: E402
from app.domain import InboundEvent  # noqa: E402
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


class FakeTts:
    def __init__(self, path: str) -> None:
        self.path = path
        self.calls: list[str] = []

    async def speak(self, text: str) -> str | None:
        self.calls.append(text)
        return self.path


async def main() -> None:
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "tts.db")
    store.init()
    cfg = load_config()
    cfg.allowed_users = {"2090127048"}
    cfg.owner_users = {"2090127048"}
    sessions = SessionService(store, cfg)
    router = Router(cfg, store, sessions, {"chat": EchoAdapter(cfg.agents["chat"])}, {})

    media = pathlib.Path(tempfile.mkdtemp()) / "reply.silk"
    media.write_bytes(b"fake")
    tts = FakeTts(str(media))
    router.tts = tts

    ev = InboundEvent(
        channel="qq_onebot",
        conv_id="u:2090127048",
        user_id="2090127048",
        text="/voice 你好呀",
        is_private=True,
    )
    actions = [a async for a in router.stream(ev)]
    check("生成了 voice 动作", bool(actions) and actions[0].kind == "voice", str(actions))
    check("文本传给了 TTS", tts.calls == ["你好呀"], str(tts.calls))
    check("media 指向文件", actions[0].media == str(media), str(actions[0].media))

    await asyncio.sleep(1.1)     # 绕过同一用户 1 秒节流

    ev2 = InboundEvent(
        channel="qq_onebot",
        conv_id="u:2090127048",
        user_id="2090127048",
        text="/voice",
        is_private=True,
    )
    actions = [a async for a in router.stream(ev2)]
    check("空文本给用法", bool(actions) and "格式" in actions[0].text, str(actions))

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    asyncio.run(main())
