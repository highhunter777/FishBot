"""语音转文字：QQ 的语音是 SILK，先转 wav，再用 faster-whisper 转写。

依赖是可选的：没装 faster-whisper、或者模型目录不存在时静默降级，
调用方拿到空串，消息里只标记「[语音]」。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from .config import ROOT, Config

log = logging.getLogger(__name__)

SILK_MAGIC = b"#!SILK_V3"
_model = None
_failed = False


def _model_dir(cfg: Config) -> Path:
    path = Path(cfg.stt.model)
    return path if path.is_absolute() else ROOT / path


def ready(cfg: Config) -> bool:
    return bool(cfg.stt.enabled) and _model_dir(cfg).is_dir()


def _load(cfg: Config):
    global _model, _failed
    if _model is not None or _failed:
        return _model
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(str(_model_dir(cfg)), device="cpu", compute_type="int8")
        log.info("语音模型已加载：%s", _model_dir(cfg))
    except Exception as exc:
        _failed = True
        log.warning("语音模型加载失败（%s），语音消息不做转写", exc)
    return _model


def _to_wav(src: Path) -> Path | None:
    """SILK 用 pilk 转 wav；已经是普通音频就原样返回。"""
    try:
        head = src.read_bytes()[:16]
    except OSError:
        return None
    if SILK_MAGIC not in head:
        return src
    try:
        import pilk

        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        out = Path(name)
        pilk.silk_to_wav(str(src), str(out))
        return out
    except Exception as exc:
        log.warning("SILK 解码失败：%s", exc)
        return None


def _pcm16k(path: Path):
    """转成 16k 单声道 float32 数组（faster-whisper 直接吃数组可绕开 av 版本问题）。"""
    import av
    import numpy as np

    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            return None
        resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
        frames = []
        for frame in container.decode(stream):
            frames.extend(resampler.resample(frame))
        frames.extend(resampler.resample(None))
    if not frames:
        return None
    return np.concatenate([f.to_ndarray().reshape(-1) for f in frames]).astype(np.float32)


def transcribe(path: str | Path, cfg: Config) -> str:
    model = _load(cfg)
    if model is None:
        return ""
    src = Path(path)
    if not src.exists():
        return ""

    wav = _to_wav(src)
    if wav is None:
        return ""
    try:
        audio = _pcm16k(wav)
        if audio is None or audio.size < 1600:        # 少于 0.1 秒当没说
            return ""
        segments, _info = model.transcribe(
            audio,
            language=cfg.stt.language or "zh",
            vad_filter=True,
            beam_size=5,
            initial_prompt="以下是普通话说的话，用简体中文。",
        )
        return "".join(s.text for s in segments).strip()
    except Exception as exc:
        log.warning("语音转写失败：%s", exc)
        return ""
    finally:
        if wav != src:
            wav.unlink(missing_ok=True)
