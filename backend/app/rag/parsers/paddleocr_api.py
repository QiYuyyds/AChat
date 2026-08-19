"""PaddleOCR cloud API parsers — PaddleOCR-VL-1.6 and PP-OCRv6.

Both engines call the PaddleOCR cloud jobs API using an API token.
The workflow is:
1. Submit a job (``POST /v2/ocr/jobs``) with the file + model ID
2. Poll the job status (``GET /v2/ocr/jobs/{jobId}``) until done
3. Download the JSONL result and extract Markdown

PaddleOCR-VL returns layout-aware Markdown (with tables, formulas, images).
PP-OCRv6 returns plain OCR text lines.

Adapted from Fidi-Intelli's ``paddleocr_api.py``.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0
_PADDLEOCR_API_BASE = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


class _BasePaddleOCRAPIParser(BaseDocumentProcessor):
    """Common base for PaddleOCR cloud jobs API parsers."""

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
    _model_id: str = ""
    _default_optional_payload: dict[str, bool] = {}

    def __init__(self, *, api_key: str = "", timeout: float = _DEFAULT_TIMEOUT):
        self._api_token = api_key or os.getenv("PADDLEOCR_API_TOKEN", "")
        self._api_url = os.getenv("PADDLEOCR_API_URL", _PADDLEOCR_API_BASE).rstrip("/")
        self._timeout = timeout

    def check_health(self) -> bool:
        return bool(self._api_token)

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)

        if not self._api_token:
            logger.warning("%s API token not configured", self.display_name)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

        try:
            text = self._process_via_jobs_api(data, filename)
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
            logger.warning("%s processing failed for %s: %s", self.display_name, filename, e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _process_via_jobs_api(self, data: bytes, filename: str) -> str:
        """Full PaddleOCR jobs API workflow: submit → poll → download → extract."""
        # Step 1: Submit job
        job_id = self._submit_job(data, filename)

        # Step 2: Poll for completion
        result_url = self._poll_job_result(job_id)

        # Step 3: Download JSONL result
        rows = self._download_jsonl(result_url)

        # Step 4: Extract Markdown from rows
        return self._extract_markdown(rows)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self._api_token}"}

    def _submit_job(self, data: bytes, filename: str) -> str:
        """Submit a job to the PaddleOCR API and return the job ID."""
        optional_payload = dict(self._default_optional_payload)
        payload_str = json.dumps(optional_payload, ensure_ascii=False)

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                self._api_url,
                headers=self._headers(),
                data={
                    "model": self._model_id,
                    "optionalPayload": payload_str,
                },
                files={"file": (filename, data)},
            )

        if resp.status_code != 200:
            raise RuntimeError(f"submit failed: HTTP {resp.status_code} {resp.text}")

        body = resp.json()
        if body.get("code") not in (None, 0):
            raise RuntimeError(f"submit API error: {body.get('msg', 'unknown')}")

        job_id = (body.get("data") or {}).get("jobId")
        if not job_id:
            raise RuntimeError("no jobId returned")

        return str(job_id)

    def _poll_job_result(
        self,
        job_id: str,
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
    ) -> str:
        """Poll the job status until done, return the JSON result URL."""
        start_time = time.time()

        with httpx.Client(timeout=30.0) as client:
            while time.time() - start_time < max_wait:
                resp = client.get(
                    f"{self._api_url}/{job_id}",
                    headers=self._headers(),
                )

                if resp.status_code != 200:
                    raise RuntimeError(f"status query failed: HTTP {resp.status_code}")

                body = resp.json()
                job_data = body.get("data") or {}
                state = job_data.get("state")

                if state == "done":
                    result_url = ((job_data.get("resultUrl") or {}).get("jsonUrl") or "").strip()
                    if not result_url:
                        raise RuntimeError("job completed but no jsonUrl")
                    return result_url

                if state == "failed":
                    error_msg = job_data.get("errorMsg") or "unknown error"
                    raise RuntimeError(f"job failed: {error_msg}")

                if state not in {"pending", "running"}:
                    raise RuntimeError(f"unknown job state: {state}")

                time.sleep(poll_interval)

        raise RuntimeError("job timed out")

    def _download_jsonl(self, json_url: str) -> list[dict[str, Any]]:
        """Download and parse the JSONL result file."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(json_url)

        if resp.status_code != 200:
            raise RuntimeError(f"download failed: HTTP {resp.status_code}")

        rows: list[dict[str, Any]] = []
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

        if not rows:
            raise RuntimeError("empty result")

        return rows

    def _extract_markdown(self, rows: list[dict[str, Any]]) -> str:
        """Extract markdown from JSONL rows — overridden by subclasses."""
        raise NotImplementedError


class PaddleOCRVLParser(_BasePaddleOCRAPIParser):
    """OCR engine backed by PaddleOCR-VL-1.6 cloud API.

    Returns layout-aware Markdown with tables, formulas, and image references.
    """

    service_name = "paddleocr_vl"
    display_name = "PaddleOCR-VL-1.6 (cloud)"
    _model_id = "PaddleOCR-VL-1.6"
    _default_optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    def _extract_markdown(self, rows: list[dict[str, Any]]) -> str:
        """Extract layout-aware Markdown from PaddleOCR-VL JSONL results.

        Each row contains ``result.layoutParsingResults``, where each item
        has a ``markdown.text`` field (the page's Markdown content) and
        optionally a ``markdown.images`` dict mapping image paths to URLs.

        Image URLs are downloaded and saved to a temp directory, and the
        markdown links are rewritten to point to the local files.
        """
        import tempfile

        markdown_parts: list[str] = []
        image_dir = tempfile.mkdtemp(prefix="paddleocr_vl_images_")

        for row in rows:
            result = row.get("result") or {}
            for item in result.get("layoutParsingResults") or []:
                markdown = item.get("markdown") or {}
                text = markdown.get("text")
                if not isinstance(text, str):
                    continue

                # Process images: download and rewrite links
                images = markdown.get("images") or {}
                for img_path, img_url in images.items():
                    if not img_path or not img_url:
                        continue
                    try:
                        local_path = self._download_image(
                            str(img_url), str(img_path), image_dir,
                        )
                        if local_path:
                            text = text.replace(f"]({img_path})", f"]({local_path})")
                            text = text.replace(str(img_url), local_path)
                    except Exception as e:
                        logger.warning("PaddleOCR-VL: image download failed: %s", e)

                if text.strip():
                    markdown_parts.append(text.strip())

        return "\n\n".join(markdown_parts).strip()

    def _download_image(self, img_url: str, img_path: str, output_dir: str) -> str:
        """Download an image from URL and save to output_dir, return relative path."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(img_url)

        if resp.status_code != 200:
            return ""

        filename = Path(img_path).name or "paddleocr_image"
        suffix = Path(filename).suffix
        if not suffix:
            content_type = resp.headers.get("Content-Type") or ""
            suffix = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".jpg"
            filename = f"{filename}{suffix}"

        timestamp = int(time.time() * 1000000)
        local_name = f"{timestamp}_{filename}"
        local_path = Path(output_dir) / local_name
        local_path.write_bytes(resp.content)

        return f"ocr-images/{local_name}"


class PaddleOCRPPOCRv6Parser(_BasePaddleOCRAPIParser):
    """OCR engine backed by PaddleOCR PP-OCRv6 cloud API.

    Returns plain OCR text lines (no layout analysis).
    """

    service_name = "paddleocr_pp_ocrv6"
    display_name = "PaddleOCR PP-OCRv6 (cloud)"
    _model_id = "PP-OCRv6"
    _default_optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useTextlineOrientation": False,
    }

    def _extract_markdown(self, rows: list[dict[str, Any]]) -> str:
        """Extract plain OCR text from PP-OCRv6 JSONL results.

        Each row contains ``result.ocrResults``, where each item has a
        ``prunedResult.rec_texts`` list of recognized text strings.
        """
        lines: list[str] = []

        for row in rows:
            result = row.get("result") or {}
            for item in result.get("ocrResults") or []:
                pruned_result = item.get("prunedResult") or {}
                rec_texts = pruned_result.get("rec_texts") or []
                for text in rec_texts:
                    if isinstance(text, str) and text.strip():
                        lines.append(text.strip())

        return "\n".join(lines).strip()
