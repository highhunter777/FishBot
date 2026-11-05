"""文档文本抽取：把 QQ 收进来的文件转成可进会话的文字。

支持 PDF / DOCX / XLSX / PPTX 和常见纯文本格式；解析库缺失或文件加密时
返回空串，由调用方降级为「只留文件路径，交给 agent 自己看」。
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
    ".yaml", ".yml", ".ini", ".toml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".sh", ".ps1",
}
KNOWN_EXTS = TEXT_EXTS | {".pdf", ".docx", ".xlsx", ".pptx"}


def extension(path: str | Path, name: str = "") -> str:
    """优先用用户看到的文件名后缀，避免下载临时名缺后缀。"""
    for candidate in (name, str(path)):
        suffix = Path(candidate).suffix.lower()
        if suffix:
            return suffix
    return ""


def supported(path: str | Path, name: str = "") -> bool:
    return extension(path, name) in KNOWN_EXTS


def extract(
    path: str | Path,
    name: str = "",
    *,
    max_chars: int = 20000,
    max_pages: int = 30,
) -> str:
    """抽取正文；不支持的格式或解析失败时返回空串。"""
    p = Path(path)
    suffix = extension(p, name)
    if suffix not in KNOWN_EXTS:
        return ""
    try:
        if suffix == ".pdf":
            text = _pdf(p, max_pages)
        elif suffix == ".docx":
            text = _docx(p)
        elif suffix == ".xlsx":
            text = _xlsx(p, max_pages)
        elif suffix == ".pptx":
            text = _pptx(p, max_pages)
        else:
            text = _text(p)
    except Exception as exc:
        log.warning("文档解析失败（%s）：%s %s", suffix or "未知", type(exc).__name__, p.name)
        return ""
    text = _clean(text)
    return text[:max_chars]


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(path: Path, max_pages: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return ""
    parts: list[str] = []
    for index, page in enumerate(reader.pages):
        if index >= max_pages:
            break
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _docx(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append("\t".join(cells))
    return "\n".join(parts)


def _xlsx(path: Path, max_rows: int) -> str:
    from openpyxl import load_workbook

    book = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in book.worksheets:
            parts.append(f"# {sheet.title}")
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= max_rows:
                    parts.append("…（表格行数已截断）")
                    break
                cells = ["" if value is None else str(value) for value in row]
                if any(cell.strip() for cell in cells):
                    parts.append("\t".join(cells))
    finally:
        book.close()
    return "\n".join(parts)


def _pptx(path: Path, max_slides: int) -> str:
    from pptx import Presentation

    deck = Presentation(str(path))
    parts: list[str] = []
    for index, slide in enumerate(deck.slides):
        if index >= max_slides:
            break
        parts.append(f"# 第 {index + 1} 页")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and str(text).strip():
                parts.append(str(text).strip())
    return "\n".join(parts)


def _text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    if zipfile.is_zipfile(path):     # 伪装成文本的 Office 包，别硬解
        return ""
    return data.decode("utf-8", "ignore")
