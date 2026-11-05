"""相关性召回：向量优先，失败退回字级 n-gram + IDF。

向量用本地 BAAI/bge-small-zh-v1.5（见 app/embed.py），按会话预计算。
两者都叠加时效衰减：越久远的内容权重越低。
"""
from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter

import numpy as np

from .store import Store

log = logging.getLogger(__name__)

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
    def __init__(self, store: Store, cfg=None, encoder=None) -> None:
        self.store = store
        self.cfg = cfg
        self._encoder = encoder

    def _embed(self, texts: list[str]) -> np.ndarray | None:
        if self._encoder is not None:
            return self._encoder(texts)
        if self.cfg is None or not self.cfg.memory.embedding:
            return None
        try:
            from . import embed

            return embed.encode(texts, self.cfg.memory.embedding_model)
        except Exception as exc:
            log.warning("向量召回不可用：%s", exc)
            return None

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

        lex_scores, max_lex = self._lexical(rows, query_grams)
        decay_days = self.cfg.memory.recall_decay_days if self.cfg else 30.0
        now = time.time()

        vectors = [self._decode(row.get("vector")) for row in rows]
        has_vectors = sum(1 for vec in vectors if vec is not None)
        query_vec = None
        if has_vectors >= max(3, len(rows) // 3):
            emb = self._embed([query])
            if emb is not None and len(emb):
                query_vec = emb[0]

        scored: list[tuple[float, dict]] = []
        for row, vec, lex in zip(rows, vectors, [lex_scores.get(r["id"], 0.0) for r in rows]):
            age_days = max(0.0, now - float(row.get("ts") or now)) / 86400
            recency = math.exp(-age_days / max(1.0, decay_days))
            lex_norm = lex / max_lex if max_lex else 0.0

            if query_vec is not None:
                cos = float(np.dot(query_vec, vec)) if vec is not None else 0.0
                score = 0.85 * cos + 0.15 * lex_norm + 0.10 * recency
                if score >= 0.45:
                    scored.append((score, row))
            elif lex >= min_score:
                # 字面召回保持原阈值，时效只做排序加权
                scored.append((lex + 0.1 * recency, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:k]]

    @staticmethod
    def _decode(blob) -> np.ndarray | None:
        if not blob:
            return None
        try:
            return np.frombuffer(blob, dtype=np.float32)
        except Exception:
            return None

    def _lexical(self, rows: list[dict], query_grams: set[str]) -> tuple[dict[int, float], float]:
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

        scores: dict[int, float] = {}
        for row, doc_grams in docs:
            common = doc_grams & query_grams
            if not common:
                continue
            # 必须至少有一个 2-gram / 英文词重合；只有单字重合的噪声太大
            if not any(len(g) >= 2 for g in common):
                continue
            weight = sum(
                (math.log((total + 1) / (df[g] + 1)) + 1.0) * _weight(g) for g in common
            )
            scores[row["id"]] = weight / (1.0 + math.log(len(doc_grams)))
        return scores, max(scores.values(), default=0.0)
