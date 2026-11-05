"""记忆维护测试：LLM 摘要 + 事实抽取 + 降级。python tests/memory_test.py"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.credentials import inject_dsh_refs  # noqa: E402
from app.domain import InboundEvent  # noqa: E402
from app.memory import MemoryService, _parse_json  # noqa: E402
from app.session import SessionService  # noqa: E402
from app.store import Store  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def test_parse_json() -> None:
    print("JSON 容错")
    check("纯 JSON", _parse_json('{"a": "b"}') == {"a": "b"})
    check("代码块", _parse_json('```json\n{"a": "b"}\n```') == {"a": "b"})
    check("前后废话", _parse_json('好的：{"a": "b"} 就这样') == {"a": "b"})
    check("坏输入", _parse_json("没有 JSON") is None)


def test_degrade() -> None:
    print("无模型时降级")
    cfg = load_config()
    cfg.agents["chat"].api_key = ""
    cfg.agents["chat"].api_key_env = "NO_SUCH_KEY_FOR_TEST"
    svc = MemoryService(cfg)
    check("未就绪", not svc.ready)

    async def go() -> str | None:
        return await svc.summarize("旧摘要", [{"role": "user", "content": "你好"}])

    check("摘要在无模型时返回 None", asyncio.run(go()) is None)


async def test_end_to_end() -> None:
    print("端到端（真调模型）")
    inject_dsh_refs()
    cfg = load_config()
    if not cfg.agents["chat"].resolve_key():
        print("  跳过：chat 没有可用 key")
        return

    tmp = pathlib.Path(tempfile.mkdtemp()) / "m.db"
    store = Store(tmp)
    store.init()
    memory = MemoryService(cfg)
    sessions = SessionService(store, cfg, memory)

    scope = "test_sum"
    ev = InboundEvent(channel="cli", conv_id="sum", user_id="u1", text="我们决定把部署改到 Docker，周五之前要跑通",
                      is_private=True)

    # 造 45 条历史，触发压缩
    for i in range(45):
        store.add_message("cli", "sum", "u1", "user" if i % 2 == 0 else "assistant", f"第 {i} 条：讨论部署方案细节", scope=scope)
    store.set_summary("cli", "sum", "之前聊过部署", scope)

    sessions.remember(ev, "好，周五前跑通。", scope)
    await sessions.flush()

    summary = store.get_summary("cli", "sum", scope)
    print("  摘要：", summary[:120].replace("\n", " "))
    check("生成了摘要", bool(summary))
    check("不是规则拼接", "第 1 条：讨论部署方案细节" not in summary, summary[:80])
    check("长度受控", len(summary) <= 620, str(len(summary)))
    check("剩余条数受控", store.count_messages("cli", "sum", scope) <= 21)

    # 事实抽取
    ev2 = InboundEvent(channel="cli", conv_id="fact", user_id="u2", text="我叫老王，在做 FishBot 这个项目，以后都叫我老王",
                       is_private=True)
    sessions.remember(ev2, "好，记住了。", scope)
    await sessions.flush()
    profile = store.profile("u2", scope)
    print("  画像：", profile)
    check("抽到了事实", bool(profile), str(profile))
    check("含称呼", any("王" in str(v) for v in profile.values()), str(profile))


def main() -> None:
    test_parse_json()
    test_degrade()
    asyncio.run(test_end_to_end())
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
