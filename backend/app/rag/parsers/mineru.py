"""MinerU parser — self-hosted MinerU service via HTTP API.

MinerU is a document parsing service that performs layout analysis + OCR
and returns results as a ZIP archive containing ``full.md`` + ``images/``.

This parser calls the ``/file_parse`` endpoint (not ``/ocr``) which returns
a ZIP with the full Markdown content and extracted images. The ZIP is then
processed to extract the markdown and save images to the workspace.

Adapted from Fidi-Intelli's ``mineru.py``.
"""

from __future__ import annotations

import logging
import tempfile

import httpx

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult
from app.rag.parsers.ocr_zip_utils import process_ocr_zip

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 1800.0  # 30 min — MinerU can be slow on large PDFs


class MinerUParser(BaseDocumentProcessor):
    """OCR engine backed by a self-hosted MinerU HTTP API.

    Calls ``POST /file_parse`` with multipart file upload. The response is
    a ZIP archive containing ``full.md`` (complete Markdown with layout
    structure) and an ``images/`` directory.

    Configurable parameters (via params dict):
    - lang_list: language list (default: ["ch"])
    - backend: backend type (default: "hybrid-auto-engine")
    - parse_method: parse method (default: "auto")
    - formula_enable: enable formula parsing (default: True)
    - table_enable: enable table parsing (default: True)
    - image_analysis: enable image/chart analysis (default: True)
    """

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
    service_name = "mineru"
    display_name = "MinerU (self-hosted)"

    def __init__(self, *, api_url: str = "", timeout: float = _DEFAULT_TIMEOUT):
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._timeout = timeout

    def check_health(self) -> bool:
        return bool(self._api_url)

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)

        if not self._api_url:
            logger.warning("MinerU API URL not configured")
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

        try:
            text = self._call_mineru(data, filename)
            text = self.normalize_text(text)

            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                content=text,
                text_chars=self.rune_len(text),
                needs_ocr=not text.strip(),
            )
        except Exception as e:
            logger.warning("MinerU processing failed for %s: %s", filename, e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _call_mineru(self, data: bytes, filename: str) -> str:
        """Call MinerU /file_parse endpoint, receive ZIP, extract markdown.

        The MinerU API returns a ZIP archive containing:
        - ``full.md``: Complete Markdown with layout structure (headings,
          tables, lists, formulas)
        - ``images/``: Extracted images referenced in the markdown

        We process the ZIP to extract the markdown and save images
        to a temporary directory, rewriting image links.
        """
        form_data = {
            "lang_list": '["ch"]',
            "backend": "hybrid-auto-engine",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "image_analysis": "true",
            "return_md": "true",
            "response_format_zip": "true",
            "return_images": "true",
        }

        files = {"files": (filename, data, "application/octet-stream")}

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._api_url}/file_parse",
                data=form_data,
                files=files,
            )

        if resp.status_code != 200:
            error_detail = "unknown error"
            try:
                error_data = resp.json()
                error_detail = error_data.get("detail", str(error_data))
            except Exception:
                error_detail = resp.text or f"HTTP {resp.status_code}"
            logger.warning("MinerU HTTP error %d: %s", resp.status_code, error_detail)
            return ""

        # Response is a ZIP file
        zip_data = resp.content

        # Process ZIP: extract full.md + save images to temp dir
        image_dir = tempfile.mkdtemp(prefix="mineru_images_")
        try:
            markdown = process_ocr_zip(zip_data, image_output_dir=image_dir)

            if not markdown.strip():
                logger.warning("MinerU returned empty markdown from ZIP")
                return ""

            logger.info(
                "MinerU: extracted %d chars markdown from %s",
                len(markdown), filename,
            )
            return markdown
        except Exception as e:
            logger.warning("MinerU ZIP processing failed: %s", e)
            return ""
