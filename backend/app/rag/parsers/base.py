"""Abstract base class for all document processors (parsers).

Every parser — whether local OCR engine, HTTP API OCR, or plain text decoder —
extends ``BaseDocumentProcessor`` so the registry can treat them uniformly.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_USEFUL_PDF_TEXT_RUNES = 80
_HYPHEN_LINE_BREAK_RE = re.compile(r"([A-Za-z])-\n([A-Za-z])")


@dataclass
class ExtractedImage:
    """An extracted embedded image from a document."""

    filename: str = ""
    data: bytes = b""
    content_type: str = "image/png"
    page_index: int | None = None
    alt_text: str = ""


@dataclass
class ParseResult:
    """Result of parsing an uploaded file."""

    filename: str = ""
    content_type: str = ""
    parser: str = ""  # e.g. "plain_text" | "pdfplumber" | "rapidocr" | "docx" | ...
    content: str = ""
    pages: int = 0
    text_chars: int = 0
    needs_ocr: bool = False
    images: list[ExtractedImage] = field(default_factory=list)


class BaseDocumentProcessor(ABC):
    """Base class for all document processors.

    Subclasses MUST define:
    - ``supported_extensions``: list of file extensions (with dot, lowercase)
    - ``service_name``: short machine-readable identifier
    - ``display_name``: human-readable name for logs/UI
    - ``process_file()``: core parsing logic
    - ``check_health()``: whether the engine's dependency/config is available
    """

    supported_extensions: list[str] = []
    service_name: str = ""
    display_name: str = ""

    # ─── Public API ───────────────────────────────────────────────────────

    @abstractmethod
    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        """Parse the given file bytes into a ``ParseResult``.

        If the engine cannot extract text (e.g. scanned PDF without OCR engine),
        it should return a ``ParseResult`` with ``needs_ocr=True``.
        """

    @abstractmethod
    def check_health(self) -> bool:
        """Return ``True`` if the engine's dependency is installed and configured."""

    # ─── Shared helpers (used by subclasses) ──────────────────────────────

    @staticmethod
    def normalize_content_type(filename: str, content_type: str) -> str:
        content_type = (content_type or "").split(";")[0].strip().lower()
        if content_type:
            return content_type
        guessed, _ = mimetypes.guess_type(filename or "")
        return (guessed or "text/plain").lower()

    @staticmethod
    def get_extension(filename: str) -> str:
        return Path(filename or "").suffix.lower()

    @staticmethod
    def decode_text(data: bytes) -> str:
        """Try UTF-8 → GBK → Latin-1 in order."""
        if isinstance(data, str):
            return data
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                return data.decode(encoding)
            except Exception:
                continue
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def normalize_text(text: str) -> str:
        """Fix line endings, remove null bytes, fix hyphenated line breaks."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\x00", "")
        text = _HYPHEN_LINE_BREAK_RE.sub(r"\1\2", text)
        lines = [line.rstrip() for line in text.split("\n")]
        normalized = "\n".join(lines)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    @staticmethod
    def rune_len(text: str) -> int:
        return len(text or "")
