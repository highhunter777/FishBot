"""角色卡：解析、世界书、组装。"""

from .card import CharacterCard, LoreEntry, load, write_png
from .compose import build_system, first_message, substitute

__all__ = [
    "CharacterCard",
    "LoreEntry",
    "load",
    "write_png",
    "build_system",
    "first_message",
    "substitute",
]
