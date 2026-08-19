"""RapidOCR parser — local OCR engine using ``rapidocr`` (PP-OCRv5).

The ``rapidocr`` package (without the ``-onnxruntime`` suffix) is the
new-generation RapidOCR with PP-OCRv5 support, offering significantly
improved Chinese recognition accuracy compared to the legacy
``rapidocr-onnxruntime`` (PP-OCRv4).

This parser is NOT a core dependency — the engine is loaded via lazy
import, and if the package is not installed the parser reports
``check_health() == False``.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import tempfile

from app.rag.parsers.base import BaseDocumentProcessor, ParseResult

logger = logging.getLogger(__name__)


class RapidOCRParser(BaseDocumentProcessor):
    """OCR engine backed by ``rapidocr`` (PP-OCRv5, ONNX runtime).

    Uses the new RapidOCR API with explicit model configuration:
    - PP-OCRv5 detection + recognition
    - Chinese language
    - Mobile model type (lighter weight)
    - Configurable box threshold (default 0.3, lower = more text detected)
    """

    supported_extensions = [".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"]
    service_name = "rapidocr"
    display_name = "RapidOCR (ONNX)"

    def __init__(self, *, model_path: str = "", det_box_thresh: float = 0.3):
        self._model_path = model_path
        self._det_box_thresh = det_box_thresh
        self._ocr = None

    def _get_model_params(self) -> dict[str, object]:
        """Build RapidOCR model parameters following Fidi-Intelli's approach."""
        params: dict[str, object] = {
            "Det.box_thresh": self._det_box_thresh,
            "Cls.engine_type": "onnxruntime",
            "Rec.engine_type": "onnxruntime",
        }

        # Try to use the new rapidocr API with enum-based configuration
        try:
            from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion  # type: ignore

            params.update({
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Det.lang_type": LangDet.CH,
                "Det.model_type": ModelType.MOBILE,
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.box_thresh": self._det_box_thresh,
                "Cls.engine_type": EngineType.ONNXRUNTIME,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "Rec.lang_type": LangRec.CH,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            })
        except ImportError:
            pass

        return params

    def check_health(self) -> bool:
        try:
            import rapidocr  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False

    def _load_model(self):
        """Lazy-load the OCR model."""
        if self._ocr is not None:
            return

        from rapidocr import RapidOCR  # type: ignore

        logger.info("Loading RapidOCR PP-OCRv5 model (det_box_thresh=%s)...", self._det_box_thresh)
        self._ocr = RapidOCR(params=self._get_model_params())
        logger.info("RapidOCR PP-OCRv5 model loaded")

    def process_file(
        self, filename: str, content_type: str, data: bytes
    ) -> ParseResult:
        ct = self.normalize_content_type(filename, content_type)
        ext = self.get_extension(filename)

        try:
            self._load_model()
        except ImportError as e:
            logger.warning("RapidOCR not available: %s", e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )
        except Exception as e:
            logger.warning("RapidOCR model load failed: %s", e)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

        try:
            if ext == ".pdf":
                text_parts, pages = self._ocr_pdf(data)
            else:
                text_parts = self._ocr_image(data)
                pages = 1

            raw_text = "\n".join(text_parts)
            text = self.normalize_text(raw_text)

            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                content=text,
                pages=pages,
                text_chars=self.rune_len(text),
                needs_ocr=not text.strip(),
            )
        except Exception as e:
            logger.warning("RapidOCR processing failed for %s: %s", filename, e, exc_info=True)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser=self.service_name,
                needs_ocr=True,
            )

    def _ocr_pdf(self, data: bytes) -> tuple[list[str], int]:
        """Render each PDF page to image with pypdfium2, then OCR each page."""
        try:
            import pypdfium2 as pdfium  # type: ignore
        except ImportError:
            logger.warning("pypdfium2 not installed — cannot render PDF for RapidOCR")
            return [], 0

        pdf = pdfium.PdfDocument(io.BytesIO(data))
        try:
            num_pages = len(pdf)
            all_lines: list[str] = []

            for page_idx in range(num_pages):
                page = pdf[page_idx]
                # Default zoom=2 (≈144 DPI), matching Fidi-Intelli
                pil_image = page.render(scale=2).to_pil()
                text = self._ocr_image(pil_image)
                if text:
                    all_lines.append(f"--- Page {page_idx + 1} ---")
                    all_lines.extend(text)

                if (page_idx + 1) % 10 == 0:
                    logger.info("RapidOCR: processed %d/%d pages", page_idx + 1, num_pages)

            pdf.close()
            return all_lines, num_pages
        except Exception as e:
            logger.warning("RapidOCR PDF processing failed: %s", e)
            pdf.close()
            return [], 0

    def _ocr_image(self, image_or_data) -> list[str]:
        """OCR a single image — accepts bytes or PIL Image.

        Uses the new RapidOCR API: ``result.txts`` returns a list of
        recognized text strings directly, no manual bbox parsing needed.
        """
        # Save to temp file if needed (RapidOCR accepts file paths)
        cleanup_needed = False
        image_path = ""

        if isinstance(image_or_data, bytes):
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".png", delete=False) as f:
                f.write(image_or_data)
                image_path = f.name
            cleanup_needed = True
        elif hasattr(image_or_data, "save"):  # PIL Image
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".png", delete=False) as f:
                image_or_data.save(f)
                image_path = f.name
            cleanup_needed = True
        else:
            image_path = str(image_or_data)

        try:
            result = self._ocr(image_path)

            # New rapidocr API: result.txts is a list of recognized text strings
            if hasattr(result, "txts") and result.txts:
                return list(result.txts)

            # Fallback: try to parse legacy tuple format (ocr_res, elapse)
            if isinstance(result, tuple) and len(result) == 2:
                ocr_res = result[0]
                if ocr_res:
                    return self._extract_legacy_text(ocr_res)

            return []
        finally:
            if cleanup_needed and os.path.exists(image_path):
                with contextlib.suppress(OSError):
                    os.remove(image_path)

    @staticmethod
    def _extract_legacy_text(ocr_res) -> list[str]:
        """Extract text from legacy RapidOCR result (list of [bbox, text, conf])."""
        lines: list[str] = []
        for item in ocr_res:
            if not item or len(item) < 2:
                continue
            text = item[1]
            if text:
                lines.append(str(text))
        return lines


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    with contextlib.suppress(OSError):
        os.remove(path)
