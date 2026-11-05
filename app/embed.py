"""本地中文向量：BAAI/bge-small-zh-v1.5，CPU 推理，懒加载。

模型没装或加载失败时返回 None，调用方退回字面召回。
"""
from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

_model = None
_failed = False
_lock = threading.Lock()


def _load(model_name: str):
    global _model, _failed
    if _model is not None:
        return _model
    if _failed:
        return None
    with _lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            try:
                _model = SentenceTransformer(model_name, device="cpu", local_files_only=True)
            except TypeError:
                _model = SentenceTransformer(model_name, device="cpu")
            log.info("向量模型已加载：%s", model_name)
        except Exception as exc:
            _failed = True
            log.warning("向量模型加载失败（%s），召回退回字面匹配", exc)
        return _model


def encode(texts: list[str], model_name: str) -> np.ndarray | None:
    """返回归一化后的 float32 向量矩阵；不可用时返回 None。"""
    model = _load(model_name)
    if model is None:
        return None
    try:
        with _lock:
            rows = model.encode(
                [text[:512] for text in texts],
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        return np.asarray(rows, dtype=np.float32)
    except Exception as exc:
        log.warning("向量编码失败：%s", exc)
        return None
