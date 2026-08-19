"""PP-Structure-V3 parser — layout analysis + OCR via PaddleX HTTP API.

Uses the PaddleX PP-Structure-V3 HTTP API for document layout analysis
and content extraction. The API returns structured Markdown with tables,
formulas, and layout information.

This parser calls a PaddleX service via HTTP (not local import), so
no ``paddleocr`` Python package is needed. The service URL is configured
via ``OCR_PP_STRUCTURE_URL`` env var (default: ``http://localhost:8080``).

Adapted from Fidi-Intelli's ``pp_structure_v3.py``.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0


class PPStructureV3Parser(BaseDocumentProcessor):
    """Document layout + OCR engine backed by PaddleX PP-Structure-V3 API.

    Calls ``POST /layout-parsing`` on a PaddleX service, sending the file
    as base64. The API returns structured Markdown with:
    - Layout information (headings, paragraphs, lists)
    - Table recognition results
    - Formula recognition results
    - Extracted images

    Configurable parameters:
    - use_table_recognition: enable table recognition (default: True)
    - use_formula_recognition: enable formula recognition (default: True)
    - use_seal_recognition: enable seal recognition (default: False)
    """

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
    service_name = "pp_structure_v3"
    display_name = "PP-Structure-V3 (PaddleX)"

    def __init__(self, *, server_url: str = "", timeout: float = _DEFAULT_TIMEOUT):
        self._server_url = (
            server_url.rstrip("/")
            or os.getenv("PADDLEX_URI", "http://localhost:8080").rstrip("/")
        )
        self._endpoint = f"{self._server_url}/layout-parsing"
        self._timeout = timeout

    def check_health(self) -> bool:
        if not self._server_url:
            return False
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self._server_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)
        ext = self.get_extension(filename)

        try:
            # Encode file as base64
            file_b64 = base64.b64encode(data).decode("utf-8")

            # Determine file type: 0=PDF, 1=image
            file_type = 0 if ext == ".pdf" else 1

            # Call the layout-parsing API
            api_result = self._call_layout_api(file_b64, file_type)

            # Check for API errors
            if api_result.get("errorCode") not in (None, 0):
                err_msg = api_result.get("errorMsg", "unknown error")
                logger.warning("PP-Structure-V3 API error: %s", err_msg)
                return ParseResult(
                    filename=filename,
                    content_type=ct,
                    parser=self.service_name,
                    needs_ocr=True,
                )

            # Extract markdown from the API result
            text = self._extract_markdown(api_result)
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
            logger.warning("PP-Structure-V3 processing failed for %s: %s", filename, e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _call_layout_api(
        self,
        file_b64: str,
        file_type: int,
        use_table_recognition: bool = True,
        use_formula_recognition: bool = True,
        use_seal_recognition: bool = False,
    ) -> dict[str, Any]:
        """Call the PP-Structure-V3 layout-parsing API."""
        payload: dict[str, Any] = {"file": file_b64}

        optional_params = {
            "fileType": file_type,
            "useTableRecognition": use_table_recognition,
            "useFormulaRecognition": use_formula_recognition,
            "useSealRecognition": use_seal_recognition,
        }

        for key, value in optional_params.items():
            if value is not None:
                payload[key] = value

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                self._endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code != 200:
            logger.warning(
                "PP-Structure-V3 API request failed: HTTP %d", resp.status_code,
            )
            return {"errorCode": resp.status_code, "errorMsg": resp.text}

        return resp.json()

    @staticmethod
    def _extract_markdown(api_result: dict[str, Any]) -> str:
        """Extract markdown text from the PP-Structure-V3 API result.

        The API returns a ``result.layoutParsingResults`` list, where each
        item contains a ``markdown.text`` field with the structured Markdown
        for that page.
        """
        result_data = api_result.get("result", {})
        layout_results = result_data.get("layoutParsingResults", [])

        all_text: list[str] = []
        for page_result in layout_results:
            if not page_result:
                continue
            markdown = page_result.get("markdown", {})
            text = markdown.get("text")
            if text and isinstance(text, str):
                all_text.append(text)

        return "\n\n".join(all_text)
