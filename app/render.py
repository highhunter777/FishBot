"""渲染层：把 OutboundAction 降级成目标渠道能承受的纯文本片段。"""
from __future__ import annotations

import re

from .domain import Caps, OutboundAction

_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD = re.compile(r"(\*\*|__)(.*?)\1")
_MD_CODE = re.compile(r"(`{1,3})(.*?)\1", re.S)
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)


def strip_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1 (\2)", text)
    text = _MD_BOLD.sub(r"\2", text)
    text = _MD_CODE.sub(r"\2", text)
    text = _MD_HEAD.sub("", text)
    text = _MD_QUOTE.sub("", text)
    text = text.replace("~~", "")
    return text


def render(action: OutboundAction, caps: Caps, allow_markdown: bool = True) -> list[str]:
    """返回应实际发送的文本片段列表。媒体类动作由渠道自行处理。"""
    if action.kind in ("image", "file", "voice"):
        return []

    text = action.text or ""

    if action.kind == "approval":
        if caps.buttons:
            return [text]
        options = action.options or ["允许", "拒绝"]
        return [f"{text}\n回复 y 同意，n 拒绝（{'/'.join(options)}）"]

    if (action.kind == "markdown" or allow_markdown) and caps.markdown:
        return _chunk(text, caps.max_len)

    return _chunk(strip_markdown(text), caps.max_len)


def _chunk(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text] if text else []
    parts: list[str] = []
    buf = ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > max_len and buf:
            parts.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
        while len(buf) > max_len:
            parts.append(buf[:max_len])
            buf = buf[max_len:]
    if buf:
        parts.append(buf)
    return parts
