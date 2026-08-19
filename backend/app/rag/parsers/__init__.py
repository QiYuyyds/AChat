"""Document parser package — BaseDocumentProcessor + OCR engine implementations.

Public API:
- ``parse_document()`` — unified dispatch entry point
- ``BaseDocumentProcessor`` — abstract base class for all parsers
- ``ParseResult`` — result of parsing a single file
- ``ZipParseResult`` — aggregated result of parsing a ZIP archive
"""

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult
from app.rag.parsers.unified import parse_document
from app.rag.parsers.zip_utils import ZipParseResult

__all__ = [
    "BaseDocumentProcessor",
    "ParseResult",
    "ZipParseResult",
    "parse_document",
]
