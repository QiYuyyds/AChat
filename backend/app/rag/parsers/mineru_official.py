"""MinerU Official parser — MinerU cloud API via HTTP.

Calls the official MinerU cloud service using an API key. Unlike the
previous version that tried to get text directly, this parser:

1. Request an upload URL via ``POST /file-urls/batch``
2. Upload the file to the returned URL via HTTP PUT
3. Poll ``GET /extract-results/batch/{batch_id}`` until the task is done
4. Download the result ZIP and extract ``full.md`` + images

This returns full-structured Markdown (headings, tables, lists, formulas)
instead of plain text.
"""

from __future__ import annotations

import logging
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult
from app.rag.parsers.ocr_zip_utils import process_ocr_zip

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0
_MINERU_OFFICIAL_BASE = "https://mineru.net/api/v4"


class MinerUOfficialParser(BaseDocumentProcessor):
    """OCR engine backed by the official MinerU cloud API.

    The official MinerU API workflow:
    1. ``POST /file-urls/batch`` — request upload URL + create batch
    2. ``PUT {upload_url}`` — upload the file
    3. ``GET /extract-results/batch/{batch_id}`` — poll until done
    4. Download the result ZIP — contains ``full.md`` + ``images/``

    The result ZIP is processed to extract the full Markdown with layout
    structure and images.
    """

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
    service_name = "mineru_official"
    display_name = "MinerU Official (cloud)"

    def __init__(self, *, api_key: str = "", timeout: float = _DEFAULT_TIMEOUT):
        self._api_key = api_key
        self._timeout = timeout

    def check_health(self) -> bool:
        return bool(self._api_key)

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)

        if not self._api_key:
            logger.warning("MinerU Official API key not configured")
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

        try:
            text = self._process_via_cloud(data, filename)
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
            logger.warning("MinerU Official processing failed for %s: %s", filename, e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _process_via_cloud(self, data: bytes, filename: str) -> str:
        """Full MinerU Official workflow: upload → poll → download → extract."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        # Step 1: Request upload URL
        batch_id = self._request_upload_url(data, filename, headers)

        # Step 2: Upload file
        self._upload_file(data, filename, batch_id, headers)

        # Step 3: Poll for completion
        result = self._poll_batch_result(batch_id, headers)
        zip_url = result.get("full_zip_url")

        if not zip_url:
            logger.warning("MinerU Official: no download URL in result")
            return ""

        # Step 4: Download ZIP and extract markdown + images
        return self._download_and_extract(zip_url, filename)

    def _request_upload_url(self, data: bytes, filename: str, headers: dict) -> str:
        """Request an upload URL from MinerU Official API."""
        upload_data = {
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "files": [
                {
                    "name": filename,
                    "is_ocr": True,
                    "data_id": filename[:30],
                    "page_ranges": None,
                }
            ],
        }

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{_MINERU_OFFICIAL_BASE}/file-urls/batch",
                json=upload_data,
                headers=headers,
            )

        if resp.status_code != 200:
            logger.warning("MinerU Official: upload URL request failed: HTTP %d", resp.status_code)
            raise RuntimeError(f"upload URL request failed: HTTP {resp.status_code}")

        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"upload URL API error: {result.get('msg', 'unknown')}")

        batch_id = result["data"]["batch_id"]
        upload_urls = result["data"]["file_urls"]

        if not upload_urls:
            raise RuntimeError("no upload URL returned")

        self._upload_url = upload_urls[0]
        return batch_id

    def _upload_file(self, data: bytes, filename: str, batch_id: str, headers: dict) -> None:
        """Upload the file to the URL returned by the API."""
        upload_url = getattr(self, "_upload_url", None)
        if not upload_url:
            raise RuntimeError("no upload URL available")

        with httpx.Client(timeout=60.0) as client:
            resp = client.put(upload_url, content=data)

        if resp.status_code != 200:
            raise RuntimeError(f"file upload failed: HTTP {resp.status_code}")

        logger.info("MinerU Official: file uploaded, batch_id=%s", batch_id)

    def _poll_batch_result(
        self,
        batch_id: str,
        headers: dict,
        max_wait: int = 600,
        poll_interval: float = 5.0,
    ) -> dict:
        """Poll the batch result until the task is done or fails."""
        start_time = time.time()

        with httpx.Client(timeout=30.0) as client:
            while time.time() - start_time < max_wait:
                resp = client.get(
                    f"{_MINERU_OFFICIAL_BASE}/extract-results/batch/{batch_id}",
                    headers=headers,
                )

                if resp.status_code != 200:
                    raise RuntimeError(f"status query failed: HTTP {resp.status_code}")

                result = resp.json()
                if result.get("code") != 0:
                    raise RuntimeError(f"status query API error: {result.get('msg')}")

                extract_results = result["data"].get("extract_result", [])
                if not extract_results:
                    time.sleep(poll_interval)
                    continue

                file_result = extract_results[0]
                state = file_result.get("state")

                if state == "done":
                    logger.info("MinerU Official: task completed (batch_id=%s)", batch_id)
                    return file_result
                elif state == "failed":
                    err_msg = file_result.get("err_msg", "unknown error")
                    raise RuntimeError(f"MinerU Official task failed: {err_msg}")

                time.sleep(poll_interval)

        raise RuntimeError("MinerU Official task timed out")

    def _download_and_extract(self, zip_url: str, filename: str) -> str:
        """Download the result ZIP and extract markdown + images."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(zip_url)

        if resp.status_code != 200:
            logger.warning("MinerU Official: ZIP download failed: HTTP %d", resp.status_code)
            return ""

        zip_data = resp.content

        # Process ZIP: extract full.md + save images
        image_dir = tempfile.mkdtemp(prefix="mineru_official_images_")
        try:
            markdown = process_ocr_zip(zip_data, image_output_dir=image_dir)

            if not markdown.strip():
                # Fallback: try direct ZIP extraction
                markdown = self._fallback_extract_markdown(zip_data)

            if markdown:
                logger.info(
                    "MinerU Official: extracted %d chars markdown from %s",
                    len(markdown), filename,
                )

            return markdown
        except Exception as e:
            logger.warning("MinerU Official ZIP processing failed: %s", e)
            return self._fallback_extract_markdown(zip_data)

    @staticmethod
    def _fallback_extract_markdown(zip_data: bytes) -> str:
        """Fallback: extract the first .md file from ZIP without image processing."""
        import io

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
                md_files = [n for n in zf.namelist() if n.lower().endswith(".md")]
                if md_files:
                    md_file = next(
                        (n for n in md_files if Path(n).name == "full.md"),
                        md_files[0],
                    )
                    with zf.open(md_file) as f:
                        return f.read().decode("utf-8")
        except Exception as e:
            logger.warning("MinerU Official fallback extraction failed: %s", e)

        return ""
