"""把角色卡组装成一段 system prompt。

chat 和 agent 共用同一份文本（ACP 只收一段文本，所以示例对话也用文本形式，
避免两条通道行为不一致）。
"""
from __future__ import annotations

from . import lorebook
from .card import CharacterCard

VARS = ("char", "user", "persona", "bot")

DEFAULT_HEADER = "你在扮演一个角色，始终保持这个角色的说话方式与立场。"


def substitute(text: str, char_name: str, user_name: str) -> str:
    if not text:
        return ""
    mapping = {
        "{{char}}": char_name,
        "{{user}}": user_name,
        "{{persona}}": user_name,
        "{{bot}}": char_name,
        "<BOT>": char_name,
        "<USER>": user_name,
    }
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def build_system(
    card: CharacterCard,
    user_name: str,
    latest_text: str = "",
    extra: str = "",
    budget_chars: int = 4000,
    lore_budget: int = 1200,
) -> str:
    char_name = card.name or "助手"
    parts: list[str] = []

    head = card.system_prompt.strip() or DEFAULT_HEADER
    parts.append(substitute(head, char_name, user_name))

    if card.description.strip():
        parts.append("【设定】\n" + substitute(card.description.strip(), char_name, user_name))
    if card.personality.strip():
        parts.append("【性格】\n" + substitute(card.personality.strip(), char_name, user_name))
    if card.scenario.strip():
        parts.append("【场景】\n" + substitute(card.scenario.strip(), char_name, user_name))
    if card.mes_example.strip():
        parts.append("【对话示例，照着这个语气说】\n" + substitute(card.mes_example.strip(), char_name, user_name))

    entries = lorebook.select(card.lorebook, latest_text, budget_chars=lore_budget)
    if entries:
        body = "\n".join(substitute(e.content.strip(), char_name, user_name) for e in entries)
        parts.append("【补充设定】\n" + body)

    if card.post_history_instructions.strip():
        parts.append(substitute(card.post_history_instructions.strip(), char_name, user_name))

    if extra:
        parts.append(extra)

    text = "\n\n".join(p for p in parts if p)
    if len(text) > budget_chars:
        text = text[:budget_chars] + "…"
    return text


def first_message(card: CharacterCard, user_name: str) -> str:
    """开场白（可选，用于首次对话）。"""
    return substitute(card.first_mes.strip(), card.name or "助手", user_name)
