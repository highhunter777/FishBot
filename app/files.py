"""收发文件的暂存：按日期分目录，用到才建。

收进来的图片/文件放 <base>/inbox/YYYY-MM-DD/，要发出去的放 <base>/outbox/YYYY-MM-DD/。
<base> 就是 agent 的工作目录，这样它能直接 read 到。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import uuid
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024      # 单文件下载上限：10 MB
CHUNK = 64 * 1024

# 图片魔数 -> (扩展名, MIME)
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
    (b"BM", ".bmp", "image/bmp"),
)


def day_dir(base: str | Path, kind: str) -> Path:
    """base/inbox/2026-11-05 这种，按需创建。"""
    path = Path(base) / kind / date.today().isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _name(url: str, fallback_ext: str) -> str:
    ext = ""
    if url:
        suffix = Path(url.split("?")[0]).suffix
        if suffix and len(suffix) <= 6:
            ext = suffix
    return f"{uuid.uuid4().hex[:12]}{ext or fallback_ext}"


async def download(
    url: str,
    folder: Path,
    fallback_ext: str = ".jpg",
    max_bytes: int = MAX_BYTES,
) -> str | None:
    """下载到 folder，返回本地路径；失败或超限返回 None（不阻塞消息处理）。"""
    if not url:
        return None
    target = folder / _name(url, fallback_ext)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    log.warning("下载失败 %s：%s", resp.status_code, url[:60])
                    return None
                size = resp.headers.get("content-length", "")
                if size.isdigit() and int(size) > max_bytes:
                    log.warning("跳过超限文件 %.1f MB：%s", int(size) / 1048576, url[:60])
                    return None

                total = 0
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            fh.close()
                            target.unlink(missing_ok=True)
                            log.warning("下载超过 %.0f MB，已丢弃：%s", max_bytes / 1048576, url[:60])
                            return None
                        fh.write(chunk)
        return str(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        log.warning("下载失败：%s（%s）", type(exc).__name__, url[:60])
        return None


def sha256_file(path: str | Path) -> str:
    """按内容算指纹；同一张图被重复发送时复用 OCR/描述，不重复调用模型。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def image_mime(path: str | Path) -> str:
    """按魔数判断图片 MIME；识别不出来再退回扩展名。"""
    p = Path(path)
    try:
        head = p.open("rb").read(16)
    except OSError:
        head = b""
    for magic, _ext, mime in _IMAGE_MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def sniff_image_ext(path: str | Path) -> str | None:
    """返回真实图片扩展名；不是常见图片就返回 None。"""
    p = Path(path)
    try:
        head = p.open("rb").read(16)
    except OSError:
        return None
    for magic, ext, _mime in _IMAGE_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def ensure_image_ext(path: str | Path) -> str:
    """把 QQ 下载时统一命名的 .jpg 纠正成真实扩展名（GIF/PNG 不再被当成 JPEG）。"""
    p = Path(path)
    ext = sniff_image_ext(p)
    if not ext or p.suffix.lower() == ext:
        return str(p)
    target = p.with_suffix(ext)
    try:
        if target.exists():
            p.unlink(missing_ok=True)
        else:
            p.rename(target)
    except OSError as exc:
        log.warning("图片扩展名纠正失败：%s", exc)
        return str(p)
    return str(target)


def compress_image(
    path: str | Path, max_side: int = 1024, quality: int = 85
) -> str:
    """把图片压到长边 max_side 的 JPEG，供视觉模型/agent 读取；失败时原样返回。

    动图（GIF/WebP 多帧）不压，保留原始文件，后面做动图理解时再抽帧。
    """
    p = Path(path)
    try:
        from PIL import Image, ImageOps
    except Exception:
        return str(p)

    try:
        with Image.open(p) as im:
            if getattr(im, "is_animated", False):
                return str(p)
            mime = image_mime(p)
            if (
                mime == "image/jpeg"
                and max(im.size) <= max_side
                and p.stat().st_size <= 1_500_000
            ):
                return str(p)

            frame = ImageOps.exif_transpose(im)
            if frame.mode in ("RGBA", "LA", "P"):
                rgba = frame.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                frame = bg
            elif frame.mode != "RGB":
                frame = frame.convert("RGB")
            if max(frame.size) > max_side:
                frame.thumbnail((max_side, max_side), Image.LANCZOS)

            target = p.with_name(f"{p.stem}_s.jpg")
            frame.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size >= p.stat().st_size and max(frame.size) == max(im.size):
                target.unlink(missing_ok=True)
                return str(p)
            return str(target)
    except Exception as exc:
        log.debug("图片压缩跳过（%s）：%s", type(exc).__name__, p.name)
        return str(p)


def animation_sheet(
    path: str | Path, max_frames: int = 6, max_side: int = 512
) -> dict | None:
    """把动图抽成关键帧拼图，供视觉模型一次看完；静态图或失败返回 None。

    返回 {path, frames, duration_ms, sampled}，帧按从左到右、从上到下排列。
    """
    p = Path(path)
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(p) as im:
            frames_total = int(getattr(im, "n_frames", 1))
            if frames_total <= 1 or not getattr(im, "is_animated", False):
                return None

            count = min(max_frames, frames_total)
            if count <= 1:
                indices = [0]
            else:
                indices = sorted(
                    {round(i * (frames_total - 1) / (count - 1)) for i in range(count)}
                )

            frames = []
            durations: list[int] = []
            for index in indices:
                im.seek(index)
                durations.append(int(im.info.get("duration") or 0))
                frame = im.convert("RGB")
                frame.thumbnail((max_side, max_side), Image.LANCZOS)
                frames.append(frame.copy())

            cell_w = max(frame.width for frame in frames)
            cell_h = max(frame.height for frame in frames)
            cols = 3 if len(frames) > 4 else 2
            rows = (len(frames) + cols - 1) // cols
            canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (255, 255, 255))
            for index, frame in enumerate(frames):
                x = (index % cols) * cell_w + (cell_w - frame.width) // 2
                y = (index // cols) * cell_h + (cell_h - frame.height) // 2
                canvas.paste(frame, (x, y))

            target = p.with_name(f"{p.stem}_frames.jpg")
            canvas.save(target, "JPEG", quality=85, optimize=True)

            average = sum(durations) / len(durations) if durations else 100
            return {
                "path": str(target),
                "frames": frames_total,
                "duration_ms": int(average * frames_total),
                "sampled": len(frames),
            }
    except Exception as exc:
        log.debug("动图抽帧跳过（%s）：%s", type(exc).__name__, p.name)
        return None


def data_url(path: str | Path) -> str | None:
    """转成 data URL，给支持 vision 的接口用。"""
    p = Path(path)
    if not p.exists():
        return None
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    try:
        payload = base64.b64encode(p.read_bytes()).decode()
    except OSError:
        return None
    return f"data:{mime};base64,{payload}"
