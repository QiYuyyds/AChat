"""DeepSeek-OCR parser — SiliconFlow API via HTTP.

Calls the SiliconFlow-hosted DeepSeek-OCR model using an API key.
The model converts document images to **Markdown** (not plain text),
preserving headings, tables, lists, and other structural elements.

For PDF files, each page is rendered to an image with pypdfium2, then
sent to the API individually.
"""

from __future__ import annotations

import base64
import io
import logging
import re

import httpx

from app.rag.parsers.base import BaseDocumentProcessor, ExtractedImage, ParseResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0
_SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"

# MIME type mapping for supported image formats
_MIME_TYPE_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


class DeepSeekOCRParser(BaseDocumentProcessor):
    """OCR engine backed by DeepSeek-OCR on SiliconFlow API.

    Unlike the previous version that asked for "extracted text", this parser
    sends the prompt ``<image>\\n<|grounding|>Convert the document to markdown.``
    . The model returns structured
    Markdown with headings, tables, and lists.

    For PDFs, each page is rendered to a PNG image (via pypdfium2) and sent
    to the API individually, then the results are joined.
    """

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"]
    service_name = "deepseek_ocr"
    display_name = "DeepSeek-OCR (SiliconFlow)"

    def __init__(self, *, api_key: str = "", timeout: float = _DEFAULT_TIMEOUT):
        self._api_key = api_key
        self._timeout = timeout

    def check_health(self) -> bool:
        return bool(self._api_key)

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)
        ext = self.get_extension(filename)

        if not self._api_key:
            logger.warning("DeepSeek-OCR API key not configured")
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

        try:
            if ext == ".pdf":
                text, images = self._process_pdf(data)
            else:
                mime_type = _MIME_TYPE_MAP.get(ext, "image/jpeg")
                text = self._call_api(data, mime_type)
                images = []

            text = self.normalize_text(text)

            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                content=text,
                text_chars=self.rune_len(text),
                needs_ocr=not text.strip(),
                images=images,
            )
        except Exception as e:
            logger.warning("DeepSeek-OCR processing failed for %s: %s", filename, e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _process_pdf(self, data: bytes) -> tuple[str, list[ExtractedImage]]:
        """Render each PDF page to image, then OCR each page via API.

        Returns tuple of (joined markdown text, list of page images).
        """
        try:
            import pypdfium2 as pdfium  # type: ignore
        except ImportError:
            logger.warning("pypdfium2 not installed — cannot render PDF pages for DeepSeek-OCR")
            return "", []

        pdf = pdfium.PdfDocument(io.BytesIO(data))
        try:
            total_pages = len(pdf)
            all_text: list[str] = []
            images: list[ExtractedImage] = []

            for page_idx in range(total_pages):
                page = pdf[page_idx]
                # 200 DPI = scale 200/72 ≈ 2.78
                bitmap = page.render(scale=200 / 72)
                pil_image = bitmap.to_pil()

                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                img_bytes = buf.getvalue()

                page_text = self._call_api(img_bytes, "image/png")
                if page_text.strip():
                    all_text.append(page_text)

                images.append(ExtractedImage(
                    filename=f"page_{page_idx}.png",
                    data=img_bytes,
                    content_type="image/png",
                    page_index=page_idx,
                ))

                if (page_idx + 1) % 10 == 0:
                    logger.info(
                        "DeepSeek-OCR: processed %d/%d pages", page_idx + 1, total_pages,
                    )

            pdf.close()
            return "\n\n".join(all_text), images
        except Exception as e:
            logger.warning("DeepSeek-OCR PDF processing failed: %s", e)
            pdf.close()
            return "", []

    def _call_api(self, data_bytes: bytes, mime_type: str) -> str:
        """Call SiliconFlow DeepSeek-OCR API with a single image.

        The prompt uses:
        ``<image>\\n<|grounding|>Convert the document to markdown.``

        After getting the response, special grounding tags (``<|ref|>...<|/ref|>``,
        ``<|det|>...<|/det|>``) are removed — these are DeepSeek-OCR's
        bounding-box annotations, not part of the document content.
        """
        encoded = base64.b64encode(data_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{encoded}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {
                        "type": "text",
                        "text": "<image>\n<|grounding|>Convert the document to markdown. ",
                    },
                ],
            }
        ]

        payload = {
            "model": "deepseek-ai/DeepSeek-OCR",
            "messages": messages,
        }

        resp = httpx.post(
            f"{_SILICONFLOW_BASE}/chat/completions",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()

        choices = body.get("choices", [])
        text = ""
        if choices:
            msg = choices[0].get("message", {})
            text = msg.get("content", "")

        # Clean up DeepSeek-OCR grounding tags
        text = re.sub(r"<\|ref\|>.*?<\|/ref\|>", "", text)
        text = re.sub(r"<\|det\|>.*?<\|/det\|>", "", text)

        return text.strip()
