"""语音回复：edge-tts 生成 mp3，转成腾讯 SILK 供 OneBot 发送。"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

from .config import ROOT, Config

log = logging.getLogger(__name__)

OUT_DIR = ROOT / "data" / "tts"


class Tts:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return bool(self.cfg.tts.enabled)

    async def speak(self, text: str) -> str | None:
        text = (text or "").strip()
        if not text or not self.ready:
            return None
        text = text[: self.cfg.tts.max_chars]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        mp3 = Path(name)
        try:
            import edge_tts

            communicate = edge_tts.Communicate(
                text,
                self.cfg.tts.voice,
                rate=self.cfg.tts.rate,
                proxy=self.cfg.tts.proxy or None,
            )
            await communicate.save(str(mp3))
            silk = OUT_DIR / f"{uuid.uuid4().hex[:10]}.silk"
            await asyncio.to_thread(self._mp3_to_silk, mp3, silk)
            return str(silk)
        except Exception as exc:
            log.warning("TTS 生成失败：%s", exc)
            return None
        finally:
            try:
                mp3.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _mp3_to_silk(mp3: Path, silk: Path) -> None:
        import av
        import pilk

        fd, pcm_name = tempfile.mkstemp(suffix=".pcm")
        os.close(fd)
        pcm = Path(pcm_name)
        try:
            with av.open(str(mp3)) as container:
                stream = next((s for s in container.streams if s.type == "audio"), None)
                if stream is None:
                    raise RuntimeError("mp3 里没有音频流")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
                frames = []
                for frame in container.decode(stream):
                    frames.extend(resampler.resample(frame))
                frames.extend(resampler.resample(None))
            if not frames:
                raise RuntimeError("音频解码为空")
            with pcm.open("wb") as handle:
                for frame in frames:
                    handle.write(frame.to_ndarray().tobytes())
            pilk.encode(str(pcm), str(silk), pcm_rate=24000, silk_rate=24000, tencent=True)
        finally:
            try:
                pcm.unlink(missing_ok=True)
            except OSError:
                pass
