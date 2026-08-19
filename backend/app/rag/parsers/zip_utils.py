"""ZIP batch upload unpacking utilities.

Provides ``unpack_zip`` which extracts files from a ZIP archive and
calls ``parse_document`` recursively for each contained file.

Safety limits:
- Maximum nesting depth: 3 levels (nested ZIPs inside ZIPs)
- Maximum file count per archive: 1000
- Maximum uncompressed total size: 500 MB
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.rag.parsers.base import ParseResult

logger = logging.getLogger(__name__)

MAX_NESTING_DEPTH = 3
MAX_FILE_COUNT = 1000
MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024  # 500 MB


@dataclass
class ZipParseResult:
    """Aggregated result of parsing all files inside a ZIP archive."""

    filename: str = ""
    content_type: str = ""
    parser: str = "zip"
    results: list[ParseResult] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    needs_ocr: bool = False

    @property
    def content(self) -> str:
        """Concatenated text from all successfully parsed files."""
        parts: list[str] = []
        for r in self.results:
            if r.content.strip():
                header = f"--- {r.filename} ---"
                parts.append(f"{header}\n{r.content}")
        return "\n\n".join(parts)

    @property
    def text_chars(self) -> int:
        return sum(r.text_chars for r in self.results)

    @property
    def pages(self) -> int:
        return sum(r.pages for r in self.results)


def unpack_zip(
    filename: str,
    content_type: str,
    data: bytes,
    *,
    ocr_engine: str = "auto",
    depth: int = 0,
) -> ZipParseResult:
    """Unpack a ZIP archive and parse each contained file.

    Recursively parses nested ZIPs up to ``MAX_NESTING_DEPTH`` levels.
    Each file is dispatched through ``parse_document``.
    """
    result = ZipParseResult(filename=filename, content_type=content_type)

    if depth >= MAX_NESTING_DEPTH:
        result.errors.append({
            "filename": filename,
            "error": f"maximum nesting depth ({MAX_NESTING_DEPTH}) exceeded",
        })
        logger.warning("ZIP nesting depth exceeded for %s", filename)
        return result

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        result.errors.append({
            "filename": filename,
            "error": f"bad zip file: {e}",
        })
        logger.warning("Bad ZIP file %s: %s", filename, e)
        return result

    namelist = archive.namelist()
    if len(namelist) > MAX_FILE_COUNT:
        result.errors.append({
            "filename": filename,
            "error": f"file count {len(namelist)} exceeds limit {MAX_FILE_COUNT}",
        })
        logger.warning("ZIP file count exceeded for %s: %d", filename, len(namelist))
        return result

    total_uncompressed = 0

    for name in namelist:
        if name.endswith("/"):
            continue  # Skip directories

        try:
            file_info = archive.getinfo(name)
            total_uncompressed += file_info.file_size

            if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                result.errors.append({
                    "filename": name,
                    "error": "uncompressed size exceeds limit",
                })
                logger.warning("ZIP uncompressed size exceeded for %s", filename)
                break

            file_data = archive.read(name)
        except Exception as e:
            result.errors.append({"filename": name, "error": str(e)})
            logger.warning("Failed to read %s from ZIP %s: %s", name, filename, e)
            continue

        # Recursively parse each file
        sub_result = _parse_zip_entry(name, file_data, ocr_engine, depth)
        if isinstance(sub_result, ZipParseResult):
            # Nested ZIP
            result.results.extend(sub_result.results)
            result.errors.extend(sub_result.errors)
            if sub_result.needs_ocr:
                result.needs_ocr = True
        elif isinstance(sub_result, ParseResult):
            result.results.append(sub_result)
            if sub_result.needs_ocr:
                result.needs_ocr = True

    return result


def _parse_zip_entry(
    name: str,
    file_data: bytes,
    ocr_engine: str,
    depth: int,
) -> ParseResult | ZipParseResult:
    """Parse a single file extracted from a ZIP archive."""
    ext = Path(name).suffix.lower()

    if ext == ".zip":
        # Nested ZIP — recurse
        return unpack_zip(
            name,
            "application/zip",
            file_data,
            ocr_engine=ocr_engine,
            depth=depth + 1,
        )

    # Use the registry's parse_document for all other file types
    from app.rag.parser_registry import parse_document

    ct = _guess_content_type(name)
    try:
        return parse_document(name, ct, file_data, ocr_engine=ocr_engine)
    except Exception as e:
        logger.warning("Failed to parse %s from ZIP: %s", name, e)
        return ParseResult(
            filename=name,
            content_type=ct,
            parser="error",
            needs_ocr=False,
        )


def _guess_content_type(filename: str) -> str:
    """Guess MIME type from filename."""
    import mimetypes

    ct, _ = mimetypes.guess_type(filename or "")
    return (ct or "application/octet-stream").lower()
