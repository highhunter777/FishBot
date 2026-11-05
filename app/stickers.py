"""表情素材：文件名即键名，模型用 [表情:键名] 触发。

素材放 assets/stickers/，一张图一个键（png / jpg / gif / webp 都行）。
文件名就是键名，比如 `无语.png` → `[表情:无语]`。
想加别名就写 index.json 的 aliases，不想写也能用。
"""
from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

DIR = ROOT / "assets" / "stickers"
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MARKER = re.compile(r"[\[【]\s*表情\s*[:：]\s*([^\]】\n]{1,20}?)\s*[\]】]")

# 说明只用于给模型的提示，不进调用逻辑
NOTES = {
    "开心": "心情好、被夸了",
    "大笑": "真的好笑",
    "害羞": "被夸到不好意思、被摸头",
    "无语": "对方说了蠢话、或自己接不上",
    "疑惑": "没听懂、要确认",
    "震惊": "听到意外的事",
    "生气": "有点恼，但不真发火",
    "委屈": "被凶了、事情没做成",
    "哭": "很难过、撒娇式哭",
    "得意": "做成了事、想被表扬",
    "慌张": "出岔子了、来不及了",
    "叹气": "无奈、累了",
    "思考": "正在琢磨",
    "点头": "同意、收到",
    "摇头": "不行、不同意",
    "鞠躬": "道歉",
    "比心": "表达喜欢",
    "加油": "给对方打气",
    "偷看": "试探、偷偷瞄",
    "瘫倒": "彻底没力气了",
    "睡觉": "困了、道晚安",
    "期待": "眼巴巴等回应",
    "喵": "撒娇、强调女仆身份",
    "呆毛": "发呆、冒呆毛",
    "打招呼": "见面、响应对方",
    "摸鱼": "偷懒、闲聊",
    "点赞": "认可、赞同",
}


def _aliases() -> dict[str, str]:
    path = DIR / "index.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("表情索引读取失败：%s", exc)
        return {}
    raw = data.get("aliases") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in raw.items() if k and v}


def available() -> dict[str, Path]:
    """扫描素材目录，返回 键名 -> 文件。同键多格式时按 EXTS 顺序取第一个。"""
    if not DIR.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in sorted(DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        key = path.stem.strip()
        if key and key not in found:
            found[key] = path
    return found


def resolve(name: str) -> Path | None:
    items = available()
    key = (name or "").strip()
    if not key:
        return None
    if key in items:
        return items[key]
    alias = _aliases().get(key)
    if alias and alias in items:
        return items[alias]
    # 容错：去掉空格/标点后仍能匹配，或只命中唯一一个包含关系
    norm = re.sub(r"[\s:：_，。！？、!?\-]+", "", key).lower()
    if norm:
        for candidate in items:
            if re.sub(r"[\s:：_，。！？、!?\-]+", "", candidate).lower() == norm:
                return items[candidate]
        hits = [candidate for candidate in items if norm in candidate.lower()]
        if len(hits) == 1:
            return items[hits[0]]
    return None


def names() -> list[str]:
    return sorted(available())


def _norm(text: str) -> str:
    return re.sub(r"[\s:：_，。！？、!?\-]+", "", str(text or "")).lower()


def _entries() -> list[dict]:
    """把素材目录和 index.json 合成梗库条目：键名、路径、标签、说明。"""
    raw: dict[str, dict] = {}
    path = DIR / "index.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("items") or []:
                if isinstance(item, dict) and item.get("file"):
                    raw[Path(str(item["file"])).stem] = item
        except Exception as exc:
            log.warning("梗库索引读取失败：%s", exc)

    entries = []
    for key, file_path in available().items():
        item = raw.get(key) or {}
        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
        if key not in tags:
            tags.insert(0, key)
        note = NOTES.get(key, "")
        alt = str(item.get("alt") or note or key).strip()
        entries.append({"key": key, "path": file_path, "tags": tags, "alt": alt})
    return entries


def search(query: str, limit: int = 3) -> list[dict]:
    """按键名/标签/说明做粗匹配；返回得分最高的条目。"""
    q = _norm(query)
    if not q:
        return []
    hits: list[tuple[int, dict]] = []
    for item in _entries():
        score = 0
        key = _norm(item["key"])
        if q == key:
            score += 100
        elif q in key or key in q:
            score += 30
        for tag in item["tags"]:
            value = _norm(tag)
            if not value:
                continue
            if q == value:
                score += 20
            elif q in value or value in q:
                score += 15
        if q in _norm(item["alt"]):
            score += 10
        if q in _norm(NOTES.get(item["key"], "")):
            score += 5
        if score:
            hits.append((score, item))
    hits.sort(key=lambda pair: (-pair[0], pair[1]["key"]))
    return [item for _score, item in hits[: max(0, limit)]]


def pick_reply(query: str, recent: set[str] | None = None) -> dict | None:
    """挑一个用于斗图/回应的表情；优先语义匹配，其次随机，避开最近用过的。"""
    recent = recent or set()
    for item in search(query, limit=12):
        if item["key"] not in recent:
            return item
    candidates = [item for item in _entries() if item["key"] not in recent]
    return random.choice(candidates) if candidates else None


def extract(text: str) -> tuple[str, list[str]]:
    """把 [表情:x] 从文本里摘出来，返回 (干净文本, 命中的键名列表)。"""
    hits: list[str] = []

    def take(match: re.Match) -> str:
        hits.append(match.group(1).strip())
        return ""

    cleaned = MARKER.sub(take, text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, hits


def prompt_hint() -> str:
    """给模型看的可用表情清单（带语义），没有素材时返回空串。"""
    items = available()
    if not items:
        return ""
    parts = []
    for key in sorted(items):
        note = NOTES.get(key)
        parts.append(f"{key}（{note}）" if note else key)
    return (
        "【表情】想表达情绪时可以单独写一行 `[表情:键名]`，系统会替你发一张图。\n"
        f"可用：{'、'.join(parts)}\n"
        "规则：一次最多一条；正经汇报、报错、给步骤时不要发；别每句都发。"
    )
