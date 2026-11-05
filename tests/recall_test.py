"""相关性召回测试：python tests/recall_test.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.recall import Recall, grams  # noqa: E402
from app.store import Store  # noqa: E402

PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    PASS += 1
    print(f"  ok  {name}")


def test_grams() -> None:
    print("切词")
    g = grams("明天适合钓鱼吗")
    check("中文 bigram", "钓鱼" in g and "适合" in g, str(sorted(g)[:6]))
    check("英文词", "docker" in grams("用 Docker 部署"))
    check("停用词过滤", "什么" not in grams("这是什么"))


def build_store() -> Store:
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "r.db")
    store.init()
    old = [
        "上次说的那个野钓点在城北水库，水深大概三米",
        "周五之前要把 Docker 部署跑通，不然来不及",
        "我不喜欢吃香菜，点外卖记得备注",
        "周末想去爬山，但是天气预报说有雨",
        "帮我记一下，项目代号是小鱼",
    ]
    for text in old:
        store.add_message("cli", "c1", "u1", "user", text, scope="recall")
        store.add_message("cli", "c1", "u1", "assistant", "好", scope="recall")

    for i in range(15):
        store.add_message("cli", "c1", "u1", "user", f"第 {i} 条闲聊，跟之前无关", scope="recall")
        store.add_message("cli", "c1", "u1", "assistant", "嗯", scope="recall")
    return store


def test_recall() -> None:
    print("召回")
    store = build_store()
    recall = Recall(store)

    hits = recall.search("那个野钓点在哪", scope="recall", channel="cli", conv_id="c1")
    texts = " ".join(h["content"] for h in hits)
    print("  命中:", texts[:80])
    check("召回到钓鱼那条", "野钓点" in texts, texts[:80])
    check("没把香菜那条带出来", "香菜" not in texts, texts[:80])

    # 只有单字重合时宁可不召回（"钓鱼"与"野钓"只共享"钓"）
    hits = recall.search("钓鱼", scope="recall", channel="cli", conv_id="c1")
    check("纯单字重合不召回", not hits, str([h["content"][:20] for h in hits]))

    hits = recall.search("Docker 部署搞定了吗", scope="recall", channel="cli", conv_id="c1")
    texts = " ".join(h["content"] for h in hits)
    check("召回到部署那条", "Docker" in texts, texts[:80])

    hits = recall.search("今天吃什么", scope="recall", channel="cli", conv_id="c1")
    texts = " ".join(h["content"] for h in hits)
    check("无关问题不乱召回", "野钓点" not in texts and "Docker" not in texts, texts[:80])

    hits = recall.search("第 14 条闲聊", scope="recall", channel="cli", conv_id="c1")
    check("排除最近 N 条", all("第 14 条" not in h["content"] for h in hits), str([h["content"] for h in hits]))


def main() -> None:
    test_grams()
    test_recall()
    print(f"\n全部通过：{PASS} 项")


if __name__ == "__main__":
    main()
