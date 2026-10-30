"""Текст резюме из файла: PDF (текстовый слой, pypdf) и DOCX (word/document.xml, stdlib).

Формат определяется по сигнатуре, а не по имени или Content-Type. Лимиты защищают от
тяжёлых файлов: страниц PDF, распакованного размера DOCX (zip-бомба) и длины текста.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

MAX_PDF_PAGES = 30
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


class ResumeFileError(ValueError):
    """Файл не разобрать: формат, шифрование, нет текста."""


@dataclass(frozen=True)
class ResumeFileText:
    text: str
    format: str  # "pdf" | "docx"
    truncated: bool
    pages: int | None = None


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _pdf_text(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ResumeFileError("PDF защищён паролем — сохраните его без пароля")
        pages = reader.pages
        parts = [page.extract_text() or "" for page in list(pages)[:MAX_PDF_PAGES]]
        return "\n\n".join(parts), len(pages)
    except ResumeFileError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise ResumeFileError(f"PDF не читается: {e}") from e


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            try:
                info = z.getinfo("word/document.xml")
            except KeyError as e:
                raise ResumeFileError("Это не DOCX: нет word/document.xml") from e
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ResumeFileError("DOCX слишком большой после распаковки")
            xml = z.read(info)
    except zipfile.BadZipFile as e:
        raise ResumeFileError("DOCX повреждён") from e
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise ResumeFileError("DOCX повреждён: XML не разбирается") from e
    lines: list[str] = []
    for para in root.iter(f"{_W}p"):
        chunks: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                chunks.append(node.text)
            elif node.tag == f"{_W}tab":
                chunks.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                chunks.append("\n")
        lines.append("".join(chunks))
    return "\n".join(lines)


def extract_resume_text(data: bytes, *, max_chars: int) -> ResumeFileText:
    """Текст из PDF или DOCX; длиннее ``max_chars`` — обрезается с флагом ``truncated``."""
    pages: int | None = None
    if data.startswith(b"%PDF-"):
        raw, pages = _pdf_text(data)
        fmt = "pdf"
    elif data.startswith(b"PK\x03\x04"):
        raw = _docx_text(data)
        fmt = "docx"
    else:
        raise ResumeFileError("Поддерживаются только PDF и DOCX")
    text = _tidy(raw)
    if not text:
        if fmt == "pdf":
            raise ResumeFileError("В PDF нет текстового слоя (скан?) — вставьте текст вручную")
        raise ResumeFileError("В документе нет текста")
    truncated = len(text) > max_chars
    return ResumeFileText(text=text[:max_chars], format=fmt, truncated=truncated, pages=pages)
