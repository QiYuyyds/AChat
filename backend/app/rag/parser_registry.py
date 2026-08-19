"""Parser registry — OCR engine registration, lazy loading, and dispatch.

Defines ``PROCESSOR_TYPES`` mapping engine identifiers to their module path
and class name. The ``_load_processor`` function lazy-imports each engine,
returning ``None`` if the engine's dependencies are not installed.

``parse_document`` is the unified dispatch entry point that selects the
appropriate parser based on file type and ``ocr_engine`` configuration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.rag.parsers.base import BaseDocumentProcessor, ParseResult

logger = logging.getLogger(__name__)

# ─── Engine type → (module_path, class_name) registry ─────────────────────
# Each entry maps a machine-readable engine identifier to the module and
# class that implements BaseDocumentProcessor. Lazy import is used so
# missing OCR dependencies don't break the registry at import time.
PROCESSOR_TYPES: dict[str, tuple[str, str]] = {
    "rapidocr": ("app.rag.parsers.rapid_ocr", "RapidOCRParser"),
    "mineru": ("app.rag.parsers.mineru", "MinerUParser"),
    "mineru_official": ("app.rag.parsers.mineru_official", "MinerUOfficialParser"),
    "pp_structure_v3": ("app.rag.parsers.pp_structure_v3", "PPStructureV3Parser"),
    "deepseek_ocr": ("app.rag.parsers.deepseek_ocr", "DeepSeekOCRParser"),
    "paddleocr_vl": ("app.rag.parsers.paddleocr_api", "PaddleOCRVLParser"),
    "paddleocr_pp_ocrv6": ("app.rag.parsers.paddleocr_api", "PaddleOCRPPOCRv6Parser"),
}

# Human-readable labels for each OCR engine (used by API responses)
OCR_ENGINE_LABELS: dict[str, str] = {
    "rapidocr": "RapidOCR",
    "mineru": "MinerU",
    "mineru_official": "MinerU Official",
    "pp_structure_v3": "PP-StructureV3",
    "deepseek_ocr": "DeepSeek OCR",
    "paddleocr_vl": "PaddleOCR VL",
    "paddleocr_pp_ocrv6": "PaddleOCR PP-OCRv6",
}

# OCR engine priority order for ``auto`` mode — first available wins.
_OCR_ENGINE_PRIORITY: list[str] = [
    "rapidocr",
    "deepseek_ocr",
    "mineru",
    "mineru_official",
    "pp_structure_v3",
    "paddleocr_vl",
    "paddleocr_pp_ocrv6",
]

# File extensions that require OCR (images + scanned PDFs)
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml"}


def _load_processor(proc_type: str) -> BaseDocumentProcessor | None:
    """Lazy-import and instantiate a processor by type.

    Returns ``None`` if the engine's module can't be imported (missing
    dependency) or if ``check_health()`` returns ``False``.
    """
    entry = PROCESSOR_TYPES.get(proc_type)
    if entry is None:
        logger.warning("Unknown processor type: %s", proc_type)
        return None

    module_path, class_name = entry
    try:
        import importlib

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
    except ImportError as e:
        logger.info("Processor %s not available (dependency missing): %s", proc_type, e)
        return None
    except Exception as e:
        logger.warning("Failed to load processor %s: %s", proc_type, e)
        return None

    try:
        instance = cls(**_get_engine_kwargs(proc_type))
    except Exception as e:
        logger.warning("Failed to instantiate processor %s: %s", proc_type, e)
        return None

    if not instance.check_health():
        logger.info("Processor %s health check failed — marking unavailable", proc_type)
        return None

    return instance


def _get_engine_kwargs(proc_type: str) -> dict[str, Any]:
    """Build constructor kwargs for a processor from app settings."""
    s = get_settings()
    if proc_type == "rapidocr":
        return {"model_path": s.ocr_rapid_ocr_path}
    if proc_type == "mineru":
        return {"api_url": s.ocr_mineru_url}
    if proc_type == "mineru_official":
        return {"api_key": s.ocr_mineru_official_key}
    if proc_type == "deepseek_ocr":
        return {"api_key": s.ocr_deepseek_ocr_key}
    if proc_type == "pp_structure_v3":
        return {"server_url": s.ocr_pp_structure_url}
    if proc_type in ("paddleocr_vl", "paddleocr_pp_ocrv6"):
        return {"api_key": s.ocr_paddleocr_key}
    return {}


def get_available_ocr_engines() -> list[str]:
    """Return the list of OCR engine types that are available (health check passes)."""
    available: list[str] = []
    for proc_type in _OCR_ENGINE_PRIORITY:
        proc = _load_processor(proc_type)
        if proc is not None:
            available.append(proc_type)
    return available


def get_ocr_engine_status() -> list[dict[str, str]]:
    """Return detailed status for every registered OCR engine.

    Each entry has: id, label, available, status.
    Status is one of: ok / not_installed / not_configured / unreachable.
    """
    results: list[dict[str, str]] = []
    for proc_type in _OCR_ENGINE_PRIORITY:
        label = OCR_ENGINE_LABELS.get(proc_type, proc_type)
        entry = PROCESSOR_TYPES.get(proc_type)
        if entry is None:
            results.append({"id": proc_type, "label": label, "available": False, "status": "not_installed"})
            continue

        module_path, class_name = entry
        try:
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
        except ImportError:
            results.append({"id": proc_type, "label": label, "available": False, "status": "not_installed"})
            continue
        except Exception:
            results.append({"id": proc_type, "label": label, "available": False, "status": "not_installed"})
            continue

        try:
            instance = cls(**_get_engine_kwargs(proc_type))
        except Exception:
            results.append({"id": proc_type, "label": label, "available": False, "status": "not_configured"})
            continue

        try:
            healthy = instance.check_health()
        except Exception:
            results.append({"id": proc_type, "label": label, "available": False, "status": "unreachable"})
            continue

        if healthy:
            results.append({"id": proc_type, "label": label, "available": True, "status": "ok"})
        else:
            # check_health returned False — likely missing API key or service unreachable
            kwargs = _get_engine_kwargs(proc_type)
            has_key_param = any(v for v in kwargs.values())
            if has_key_param:
                results.append({"id": proc_type, "label": label, "available": False, "status": "unreachable"})
            else:
                results.append({"id": proc_type, "label": label, "available": False, "status": "not_configured"})

    return results


def select_ocr_engine(engine: str = "auto") -> BaseDocumentProcessor | None:
    """Select an OCR engine by name, or by priority if ``auto``.

    Returns ``None`` if no OCR engine is available.
    """
    if engine == "none":
        return None

    if engine != "auto":
        return _load_processor(engine)

    # Auto mode: try engines in priority order
    for proc_type in _OCR_ENGINE_PRIORITY:
        proc = _load_processor(proc_type)
        if proc is not None:
            return proc

    logger.info("No OCR engine available in auto mode")
    return None


def is_image_file(filename: str) -> bool:
    """Check if the file is an image that needs OCR."""
    ext = Path(filename or "").suffix.lower()
    return ext in _IMAGE_EXTENSIONS


def is_text_file(filename: str) -> bool:
    """Check if the file is a text-based file that can be decoded directly."""
    ext = Path(filename or "").suffix.lower()
    return ext in _TEXT_EXTENSIONS


def parse_document(
    filename: str, content_type: str, data: bytes, *, ocr_engine: str = "auto"
) -> ParseResult:
    """Unified dispatch entry point — selects parser by file type and config.

    Dispatch logic:
    - Text/Markdown → direct decode (no OCR)
    - PDF → three-level fallback first; if scanned, use OCR engine
    - Images → OCR engine
    - DOCX → python-docx
    - PPTX → python-pptx
    - ZIP → recursive unpacking (handled by unified.py)
    """
    # Lazy import to avoid circular dependency
    from app.rag.parser import DocumentProcessor

    ext = Path(filename or "").suffix.lower()

    # 1. Text files — direct decode (no OCR)
    if ext in _TEXT_EXTENSIONS or (ext not in _IMAGE_EXTENSIONS and ext not in {".pdf", ".docx", ".pptx", ".zip"}):
        processor = DocumentProcessor()
        return processor.process_file(filename, content_type, data)

    # 2. PDF files — three-level fallback, then OCR if needed
    if ext == ".pdf":
        processor = DocumentProcessor()
        result = processor.process_file(filename, content_type, data)
        if not result.needs_ocr:
            return result

        # PDF is scanned — try OCR engine
        ocr_proc = select_ocr_engine(ocr_engine)
        if ocr_proc is None:
            # No OCR engine available — return the needs_ocr result
            return result
        return ocr_proc.process_file(filename, content_type, data)

    # 3. Images — OCR engine
    if ext in _IMAGE_EXTENSIONS:
        ocr_proc = select_ocr_engine(ocr_engine)
        if ocr_proc is None:
            ct = BaseDocumentProcessor.normalize_content_type(filename, content_type)
            return ParseResult(
                filename=filename,
                content_type=ct,
                parser="none",
                needs_ocr=True,
            )
        return ocr_proc.process_file(filename, content_type, data)

    # 4. DOCX / PPTX — handled by DocumentProcessor
    if ext in (".docx", ".pptx"):
        processor = DocumentProcessor()
        return processor.process_file(filename, content_type, data)

    # 5. Fallback — try as text
    processor = DocumentProcessor()
    return processor.process_file(filename, content_type, data)
