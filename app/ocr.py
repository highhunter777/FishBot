"""本地 OCR：截图/报错图先识别文字，识别不到再交给视觉模型。

RapidOCR + onnxruntime，CPU 推理；依赖缺失时静默返回空串。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_engine = None
_failed = False
_lock = threading.Lock()


def ready(cfg) -> bool:
    return bool(cfg.ocr.enabled)


def _load():
    global _engine, _failed
    if _engine is not None:
        return _engine
    if _failed:
        return None
    with _lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
            log.info("OCR 模型已加载：RapidOCR")
        except Exception as exc:
            _failed = True
            log.warning("OCR 模型加载失败（%s），图片只走视觉/文字说明", exc)
        return _engine


def image_text(path: str | Path, cfg) -> str:
    """返回图片里的文字（按行拼接）；没有或不可用时返回空串。"""
    if not cfg.ocr.enabled:
        return ""
    engine = _load()
    if engine is None:
        return ""
    try:
        result, _elapse = engine(str(path))
    except Exception as exc:
        log.warning("OCR 识别失败：%s", exc)
        return ""
    if not result:
        return ""

    lines: list[str] = []
    for item in result:
        try:
            text = str(item[1]).strip()
            score = float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if text and score >= cfg.ocr.min_score:
            lines.append(text)
    return "\n".join(lines)[: cfg.ocr.max_chars]
