"""收发文件的暂存：按日期分目录，用到才建。

收进来的图片/文件放 <base>/inbox/YYYY-MM-DD/，要发出去的放 <base>/outbox/YYYY-MM-DD/。
<base> 就是 agent 的工作目录，这样它能直接 read 到。
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import uuid
from datetime import date
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


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


async def download(url: str, folder: Path, fallback_ext: str = ".jpg") -> str | None:
    """下载到 folder，返回本地路径；失败返回 None（不阻塞消息处理）。"""
    if not url:
        return None
    target = folder / _name(url, fallback_ext)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=True
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                log.warning("下载失败 %s：%s", resp.status_code, url[:60])
                return None
            target.write_bytes(resp.content)
        return str(target)
    except Exception as exc:
        log.warning("下载失败：%s（%s）", type(exc).__name__, url[:60])
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
