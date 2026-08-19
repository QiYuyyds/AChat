"""Unified parsing entry point — integrates parser_registry and zip_utils.

``parse_document()`` is the single entry point used by ``DocumentService``
and other callers. It dispatches to:
- ``parser_registry.parse_document()`` for regular files
- ``zip_utils.unpack_zip()`` for ZIP archives (returns aggregated result)
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.rag.parsers.base import ParseResult
from app.rag.parsers.zip_utils import ZipParseResult, unpack_zip

logger = logging.getLogger(__name__)


def parse_document(
    filename: str,
    content_type: str,
    data: bytes,
    *,
    ocr_engine: str = "auto",
) -> ParseResult | ZipParseResult:
    """Unified dispatch entry point for all file types.

    For ZIP files, returns a ``ZipParseResult`` with aggregated text.
    For all other files, delegates to ``parser_registry.parse_document()``.
    """
    ext = Path(filename or "").suffix.lower()

    if ext == ".zip":
        return unpack_zip(filename, content_type, data, ocr_engine=ocr_engine)

    # Delegate to parser_registry for all other file types
    from app.rag.parser_registry import parse_document as _parse

    return _parse(filename, content_type, data, ocr_engine=ocr_engine)
