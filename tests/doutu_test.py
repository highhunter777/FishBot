"""玩法层测试：梗库检索、斗图开关、群内自动回表情与频率上限。"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import stickers  # noqa: E402
from app.channel.qq_onebot import OneBotChannel  # noqa: E402
from app.config import load_config  # noqa: E402
from app.domain import InboundEvent  # noqa: E402
from app.router import Router  # noqa: E402
from app.store import Store  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def sticker_event(conv: str = "g:1", text: str = "[对方发来表情包]\n[表情含义] 无语") -> InboundEvent:
    return InboundEvent(
        channel="qq_onebot",
        conv_id=conv,
        user_id="2090127048",
        text=text,
        is_private=False,
        attachments=[{"kind": "image", "sticker": True, "path": str(stickers.available()["无语"])}],
    )


async def main() -> None:
    cfg = load_config()
    cfg.entertainment.doutu_max_per_hour = 1
    cfg.entertainment.doutu_same_cooldown_s = 600
    if "qq_onebot" in cfg.limits:
        cfg.limits["qq_onebot"].interval_s = 0.0
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    store.init()
    store.set_group_doutu("g:1", True)

    hits = stickers.search("无语", limit=1)
    check("梗库按标签命中", bool(hits) and hits[0]["key"] == "无语", str(hits[:1]))
    check("梗库无结果", stickers.search("不存在的梗", limit=1) == [])
    picked = stickers.pick_reply("无语", recent={"无语"})
    check("斗图避开刚用过的表情", bool(picked) and picked["key"] != "无语", str(picked and picked["key"]))

    router = Router(cfg, store, None, {})
    actions = [action async for action in router.stream(sticker_event())]
    check("群内自动回表情", bool(actions) and actions[0].kind == "image", str(actions))
    repeat = [action async for action in router.stream(sticker_event())]
    check("每小时上限生效", repeat == [], str(repeat))

    store.set_group_doutu("g:1", False)
    check("开关可关闭", not store.group_doutu("g:1"))

    meme = [action async for action in router._command(
        InboundEvent(channel="web", conv_id="c", user_id="u", text="/梗 无语", is_private=True),
        "/梗 无语",
    )]
    check("梗指令返回图片和说明", bool(meme) and meme[0].kind == "image" and bool(meme[0].text), str(meme))
    private = [
        action
        async for action in router._command(
            InboundEvent(channel="web", conv_id="c", user_id="u", text="/斗图 开", is_private=True),
            "/斗图 开",
        )
    ]
    check("斗图仅限群聊", bool(private) and private[0].text.startswith("群里才能"))

    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1,
        "user_id": 12345,
        "message_id": 77,
        "message": [{"type": "image", "data": {"sub_type": 1, "summary": "[动画表情]", "file": "s.jpg"}}],
    }
    channel = OneBotChannel(
        ws_url="x",
        group_requires_at=True,
        allowed_users={"12345"},
        doutu_check=lambda conv: True,
    )
    channel.self_id = "999"
    check("斗图群里表情可不 @", channel._to_event(payload) is not None)
    channel.doutu_check = lambda conv: False
    check("未开斗图仍需 @", channel._to_event(payload) is None)

    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    asyncio.run(main())
