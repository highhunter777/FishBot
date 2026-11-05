"""角色卡解析与组装测试：python tests/character_test.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.character import CharacterCard, LoreEntry, build_system, load, substitute, write_png  # noqa: E402
from app.character import lorebook  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def test_json_v2() -> None:
    print("JSON V2")
    card = load(ROOT / "config" / "characters" / "xiaoyu.json")
    check("name", card.name == "小渔", card.name)
    check("description", "钓鱼" in card.description or "渔具" in card.description)
    check("mes_example", "<START>" in card.mes_example)
    check("lorebook 条数", len(card.lorebook) == 3, str(len(card.lorebook)))
    check("常驻条目", any(e.constant for e in card.lorebook))
    check("非空判定", not card.is_empty)


def test_png() -> None:
    print("PNG 内嵌")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "a.png"
    original = CharacterCard(
        name="阿岩",
        description="{{char}} 是岩石收藏者，说话干巴巴的。",
        mes_example="{{user}}: 你好\n{{char}}: 嗯。",
        lorebook=[LoreEntry(keys=["石头"], content="阿岩有一柜子石头标本。")],
    )
    write_png(tmp, original)
    card = load(tmp)
    check("png name", card.name == "阿岩", card.name)
    check("png description", "岩石收藏者" in card.description)
    check("png mes_example", "嗯。" in card.mes_example)
    check("png lorebook", len(card.lorebook) == 1)


def test_substitute() -> None:
    print("变量替换")
    text = substitute("{{char}} 对 {{user}} 说，{{persona}} 别急", "小渔", "老王")
    check("char", "小渔" in text, text)
    check("user/persona", text.count("老王") == 2, text)
    check("兼容旧标记", substitute("<BOT> 和 <USER>", "A", "B") == "A 和 B")


def test_lorebook() -> None:
    print("世界书命中")
    card = load(ROOT / "config" / "characters" / "xiaoyu.json")
    hit = lorebook.select(card.lorebook, "明天适合钓鱼吗", budget_chars=2000)
    body = " ".join(e.content for e in hit)
    check("命中钓鱼条目", "野钓点" in body, body[:60])
    check("未命中渔具店", "老街口" not in body, body[:60])
    check("常驻必进", any(e.constant for e in hit))

    none = lorebook.select(card.lorebook, "今天天气不错", budget_chars=2000)
    check("无关话题只剩常驻", all(e.constant for e in none), str(len(none)))


def test_build_system() -> None:
    print("组装")
    card = load(ROOT / "config" / "characters" / "xiaoyu.json")
    text = build_system(card, "老王", "明天适合钓鱼吗")
    check("含角色名", "小渔" in text)
    check("含示例", "对话示例" in text)
    check("含命中设定", "野钓点" in text)
    check("变量已替换", "{{char}}" not in text and "{{user}}" not in text)
    check("长度受控", len(text) <= 4050, str(len(text)))


def main() -> None:
    test_json_v2()
    test_png()
    test_substitute()
    test_lorebook()
    test_build_system()
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
