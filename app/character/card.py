"""角色卡解析：支持 Character Card V2 / V3 的 JSON 与 PNG 内嵌形式。

PNG 里角色卡放在 tEXt chunk，key 是 `chara`（V2）或 `ccv3`（V3），值是 base64 JSON。
解析不依赖 Pillow，直接按 PNG 分块读。
"""
from __future__ import annotations

import base64
import json
import pathlib
from dataclasses import dataclass, field


@dataclass(slots=True)
class LoreEntry:
    keys: list[str] = field(default_factory=list)
    content: str = ""
    enabled: bool = True
    constant: bool = False        # 常驻，不靠关键词
    order: int = 100
    comment: str = ""


@dataclass(slots=True)
class CharacterCard:
    name: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    lorebook: list[LoreEntry] = field(default_factory=list)
    source: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.name, self.description, self.personality, self.system_prompt))


def load(path: str | pathlib.Path) -> CharacterCard:
    p = pathlib.Path(path)
    raw = p.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        card = _from_png(raw)
    else:
        card = _from_json_text(raw.decode("utf-8", "ignore"))
    card.source = str(p)
    return card


def _from_png(data: bytes) -> CharacterCard:
    texts = _png_text_chunks(data)
    for key in ("ccv3", "chara"):
        if key in texts:
            payload = texts[key].strip()
            try:
                decoded = base64.b64decode(payload).decode("utf-8", "ignore")
            except Exception:
                decoded = payload
            return _from_json_text(decoded)
    raise ValueError("这张 PNG 里没有角色卡数据（缺少 chara / ccv3 块）")


def _png_text_chunks(data: bytes) -> dict[str, str]:
    pos = 8
    out: dict[str, str] = {}
    size = len(data)
    while pos + 12 <= size:
        length = int.from_bytes(data[pos : pos + 4], "big")
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if ctype == b"tEXt":
            key, _, value = body.partition(b"\x00")
            out[key.decode("latin-1", "ignore")] = value.decode("latin-1", "ignore")
        elif ctype == b"iTXt":
            parts = body.split(b"\x00", 1)
            if len(parts) == 2:
                key = parts[0].decode("latin-1", "ignore")
                rest = parts[1]
                # iTXt: 压缩标志 + 方法 + 语言 + 翻译 + 文本
                if len(rest) > 2:
                    out[key] = rest[2:].decode("utf-8", "ignore")
        pos += 12 + length
    return out


def _from_json_text(text: str) -> CharacterCard:
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        body = data["data"]          # V2 / V3 包了一层
    else:
        body = data if isinstance(data, dict) else {}
    return _build(body)


def _build(body: dict) -> CharacterCard:
    book = body.get("character_book") or {}
    entries: list[LoreEntry] = []
    for item in book.get("entries") or []:
        if not isinstance(item, dict):
            continue
        keys = item.get("keys") or item.get("key") or []
        if isinstance(keys, str):
            keys = [keys]
        entries.append(
            LoreEntry(
                keys=[str(k) for k in keys if str(k).strip()],
                content=str(item.get("content") or ""),
                enabled=bool(item.get("enabled", True)),
                constant=bool(item.get("constant", False)),
                order=int(item.get("insertion_order") or item.get("order") or 100),
                comment=str(item.get("comment") or item.get("name") or ""),
            )
        )

    greetings = body.get("alternate_greetings") or []
    return CharacterCard(
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        personality=str(body.get("personality") or ""),
        scenario=str(body.get("scenario") or ""),
        first_mes=str(body.get("first_mes") or ""),
        mes_example=str(body.get("mes_example") or ""),
        system_prompt=str(body.get("system_prompt") or ""),
        post_history_instructions=str(body.get("post_history_instructions") or ""),
        alternate_greetings=[str(g) for g in greetings if str(g).strip()],
        lorebook=entries,
    )


# ---------- 自检用：造一张带角色卡的 PNG（也方便用户验证解析） ----------

def write_png(path: str | pathlib.Path, card: CharacterCard, size: int = 2) -> None:
    import struct

    payload = base64.b64encode(json.dumps(_to_v2(card), ensure_ascii=False).encode("utf-8"))
    raw = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, body: bytes) -> bytes:
        import zlib

        return (
            struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw += chunk(b"IHDR", ihdr)
    raw += chunk(b"tEXt", b"chara\x00" + payload)
    line = b"\x00" + b"\x40\x60\xa0" * size
    raw += chunk(b"IDAT", __import__("zlib").compress(line * size))
    raw += chunk(b"IEND", b"")
    pathlib.Path(path).write_bytes(raw)


def _to_v2(card: CharacterCard) -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": card.name,
            "description": card.description,
            "personality": card.personality,
            "scenario": card.scenario,
            "first_mes": card.first_mes,
            "mes_example": card.mes_example,
            "system_prompt": card.system_prompt,
            "post_history_instructions": card.post_history_instructions,
            "alternate_greetings": card.alternate_greetings,
            "character_book": {
                "entries": [
                    {
                        "keys": e.keys,
                        "content": e.content,
                        "enabled": e.enabled,
                        "constant": e.constant,
                        "insertion_order": e.order,
                        "comment": e.comment,
                    }
                    for e in card.lorebook
                ]
            },
        },
    }
