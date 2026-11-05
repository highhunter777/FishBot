"""世界书：按关键词命中注入设定，带字符预算。"""
from __future__ import annotations

from .card import LoreEntry


def select(entries: list[LoreEntry], text: str, budget_chars: int = 1200) -> list[LoreEntry]:
    """constant 常驻；其余看 keys 是否出现在 text 里（不区分大小写）。

    按 order 升序拼接，超出预算就停（后面的不再注入）。
    """
    if not entries:
        return []

    haystack = (text or "").lower()
    hit: list[LoreEntry] = []
    for entry in entries:
        if not entry.enabled or not entry.content.strip():
            continue
        if entry.constant:
            hit.append(entry)
            continue
        if any(k.lower() in haystack for k in entry.keys if k):
            hit.append(entry)

    hit.sort(key=lambda e: e.order)
    chosen: list[LoreEntry] = []
    used = 0
    for entry in hit:
        cost = len(entry.content)
        if used + cost > budget_chars and chosen:
            break
        chosen.append(entry)
        used += cost
    return chosen
