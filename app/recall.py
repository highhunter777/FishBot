"""相关性召回：从旧对话里捞回和当前这句有关的内容。

没有 embedding 端点可用（opencode 与本机网关都 404），所以用字级 n-gram + IDF：
中文同时切单字与 2-gram（单字兜住"钓鱼 / 野钓"这类不共词的近义），英文按词。
数据量在万级以内，实时算够快。
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .store import Store

STOP_WORDS = {
    "我们", "这个", "那个", "可以", "什么", "怎么", "就是", "还是", "但是", "因为",
    "所以", "如果", "然后", "现在", "已经", "没有", "一下", "这样", "那样", "一个",
    "不是", "这些", "那些", "知道", "觉得", "应该", "需要", "可能", "自己", "时候",
}
STOP_CHARS = set("的了是在我你他她它不和也都就要会有个上下来去说这那么什怎吗呢吧啊把被从对给还只很再又才已过没")

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9_]{3,}")


def grams(text: str) -> set[str]:
    out: set[str] = set()
    for block in _CJK.findall(text or ""):
        for ch in block:
            if ch not in STOP_CHARS:
                out.add(ch)
        for i in range(len(block) - 1):
            bigram = block[i : i + 2]
            if bigram not in STOP_WORDS:
                out.add(bigram)
    for word in _WORD.findall(text or ""):
        out.add(word.lower())
    return out


def _weight(gram: str) -> float:
    if len(gram) == 2:
        return 2.0        # 中文 2-gram
    if len(gram) >= 3:
        return 1.5        # 英文词
    return 1.0            # 中文单字，兜底用


class Recall:
    def __init__(self, store: Store) -> None:
        self.store = store

    def search(
        self,
        query: str,
        *,
        scope: str,
        channel: str,
        conv_id: str,
        k: int = 5,
        exclude_recent: int = 12,
        min_score: float = 0.5,
    ) -> list[dict]:
        query_grams = grams(query)
        if not query_grams:
            return []

        rows = self.store.searchable(channel, conv_id, scope=scope, exclude_recent=exclude_recent)
        if not rows:
            return []

        docs: list[tuple[dict, set[str]]] = []
        for row in rows:
            doc_grams = grams(row["content"])
            if doc_grams:
                docs.append((row, doc_grams))

        total = len(docs)
        df: Counter = Counter()
        for _, doc_grams in docs:
            for gram in doc_grams & query_grams:
                df[gram] += 1

        scored: list[tuple[float, dict]] = []
        for row, doc_grams in docs:
            common = doc_grams & query_grams
            if not common:
                continue
            # 必须至少有一个 2-gram / 英文词重合；只有单字重合的噪声太大
            # （实测「今天吃什么」会靠"天"/"吃"误召回"爬山/香菜"）
            if not any(len(g) >= 2 for g in common):
                continue
            weight = sum(
                (math.log((total + 1) / (df[g] + 1)) + 1.0) * _weight(g) for g in common
            )
            score = weight / (1.0 + math.log(len(doc_grams)))
            if score >= min_score:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:k]]
