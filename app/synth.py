"""表情合成：用本地字体在纯色卡片或现有表情上写一句话，不调模型。"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

OUT_DIR = ROOT / "data" / "generated"
FONTS = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
)


def _font(size: int):
    from PIL import ImageFont

    for path in FONTS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list[str] | None:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            if current:
                lines.append(current)
            current = ""
            continue
        trial = current + char
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = char
        if len(lines) > max_lines:
            return None
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    return lines


def _fit_text(draw, text: str, max_width: int, max_lines: int, sizes: tuple[int, ...]):
    for size in sizes:
        font = _font(size)
        if font is None:
            return None
        lines = _wrap(draw, text, font, max_width, max_lines)
        if lines:
            return font, lines
    return None


def render(
    text: str,
    template: str | Path | None = None,
    *,
    max_chars: int = 24,
    size: int = 512,
) -> str | None:
    """生成一张 512×512 的 PNG；失败返回 None。"""
    text = " ".join(str(text or "").split())[:max_chars]
    if not text:
        return None
    try:
        from PIL import Image, ImageDraw, ImageOps
    except Exception:
        return None

    try:
        canvas = Image.new("RGB", (size, size), (255, 249, 240))
        draw = ImageDraw.Draw(canvas)

        if template:
            with Image.open(template) as source:
                art = ImageOps.contain(source.convert("RGB"), (size, size))
            offset = ((size - art.width) // 2, (size - art.height) // 2)
            canvas.paste(art, offset)
            band = int(size * 0.28)
            overlay = Image.new("RGBA", (size, band), (0, 0, 0, 150))
            canvas.paste(overlay, (0, size - band), overlay)
            fitted = _fit_text(draw, text, size - 48, 2, (52, 46, 40, 34))
            if fitted is None:
                return None
            font, lines = fitted
            center = (size // 2, size - band // 2)
            draw.multiline_text(
                center,
                "\n".join(lines),
                font=font,
                fill=(255, 255, 255),
                anchor="mm",
                align="center",
                spacing=8,
                stroke_width=2,
                stroke_fill=(30, 30, 30),
            )
        else:
            draw.rounded_rectangle(
                (18, 18, size - 18, size - 18),
                radius=36,
                outline=(255, 158, 120),
                width=6,
            )
            fitted = _fit_text(draw, text, size - 120, 3, (68, 58, 50, 42, 36))
            if fitted is None:
                return None
            font, lines = fitted
            draw.multiline_text(
                (size // 2, size // 2),
                "\n".join(lines),
                font=font,
                fill=(48, 43, 38),
                anchor="mm",
                align="center",
                spacing=10,
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / f"{uuid.uuid4().hex[:12]}.png"
        canvas.save(target, "PNG")
        return str(target)
    except Exception as exc:
        log.warning("表情合成失败：%s", exc)
        return None
