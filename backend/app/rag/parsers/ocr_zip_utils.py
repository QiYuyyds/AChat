"""OCR ZIP processing utilities — extract markdown + images from OCR result ZIPs.

When OCR engines (MinerU, MinerU Official) return ZIP archives containing
``full.md`` + ``images/`` directory, this module extracts the markdown content,
saves images to the workspace file system, and rewrites image links.

This is adapted from Fidi-Intelli's ``zip_utils.py``, replacing MinIO with
local file system storage (AChat has no MinIO).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_DIR = "ocr-images"


def process_ocr_zip(
    zip_data: bytes,
    image_output_dir: str | Path | None = None,
) -> str:
    """Process an OCR result ZIP, return markdown with rewritten image links.

    Args:
        zip_data: Raw ZIP bytes from an OCR API response.
        image_output_dir: Directory to save extracted images. If None, images
            are kept as-is in the markdown with their original relative paths.

    Returns:
        Markdown text extracted from the ZIP, with image links rewritten
        to point to saved image files (if image_output_dir is provided).
    """
    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_data))
    except zipfile.BadZipFile as e:
        logger.warning("OCR result is not a valid ZIP: %s", e)
        return ""

    # Safety: reject path traversal
    for name in archive.namelist():
        if name.startswith("/") or name.startswith("\\"):
            logger.warning("ZIP contains unsafe path: %s", name)
            return ""
        if ".." in Path(name).parts:
            logger.warning("ZIP path contains parent reference: %s", name)
            return ""

    # Find markdown file (prefer full.md)
    md_files = [n for n in archive.namelist() if n.lower().endswith(".md")]
    if not md_files:
        logger.warning("No .md file found in OCR result ZIP")
        return ""

    md_file = next((n for n in md_files if Path(n).name == "full.md"), md_files[0])

    try:
        with archive.open(md_file) as f:
            markdown_content = f.read().decode("utf-8")
    except Exception as e:
        logger.warning("Failed to read %s from OCR ZIP: %s", md_file, e)
        return ""

    # Find and process images
    images_dir = _find_images_directory(archive, md_file)
    if images_dir and image_output_dir:
        markdown_content = _extract_and_rewrite_images(
            archive, images_dir, markdown_content, image_output_dir,
        )

    return markdown_content


def _find_images_directory(zip_file: zipfile.ZipFile, md_file_path: str) -> str | None:
    """Find the images directory in the ZIP relative to the markdown file."""
    md_parent = Path(md_file_path).parent

    candidates: list[str] = []
    if str(md_parent) != ".":
        candidates.extend([str(md_parent / "images"), str(md_parent.parent / "images")])
    candidates.append("images")

    for cand in candidates:
        cand_clean = cand.rstrip("/")
        if any(n.startswith(cand_clean + "/") for n in zip_file.namelist()):
            return cand_clean

    return None


def _extract_and_rewrite_images(
    zip_file: zipfile.ZipFile,
    images_dir: str,
    markdown_content: str,
    output_dir: str | Path,
) -> str:
    """Extract images from ZIP, save to output_dir, rewrite markdown links."""
    supported_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    image_names = [n for n in zip_file.namelist() if n.startswith(images_dir + "/")]

    # Build mapping: original path → new relative path
    image_map: dict[str, str] = {}

    for img_name in image_names:
        suffix = Path(img_name).suffix.lower()
        if suffix not in supported_extensions:
            continue

        try:
            with zip_file.open(img_name) as f:
                data = f.read()

            timestamp = int(time.time() * 1000000)
            base_name = Path(img_name).name
            new_name = f"{timestamp}_{base_name}"

            # Save image to output directory
            img_path = output_path / new_name
            img_path.write_bytes(data)

            # Build relative path for markdown
            relative_path = f"{DEFAULT_IMAGE_DIR}/{new_name}"
            original_name = Path(img_name).name
            original_path = f"images/{original_name}"

            image_map[original_path] = relative_path
            image_map[f"/{original_path}"] = relative_path
            image_map[original_name] = relative_path

            logger.debug("Saved OCR image: %s -> %s", img_name, relative_path)

        except Exception as e:
            logger.error("Failed to extract image %s: %s", img_name, e)
            continue

    if not image_map:
        return markdown_content

    return _replace_image_links(markdown_content, image_map)


def _replace_image_links(markdown_content: str, image_map: dict[str, str]) -> str:
    """Replace markdown image links using the image_map."""
    pattern = r"!\[([^\]]*)\]\(([^)]+)\)"

    def replace_link(match: re.Match[str]) -> str:
        alt_text = match.group(1) or ""
        img_path = match.group(2)

        for pattern_key, new_path in image_map.items():
            if img_path.endswith(pattern_key) or img_path == pattern_key:
                return f"![{alt_text}]({new_path})"

        filename = os.path.basename(img_path)
        if filename in image_map:
            return f"![{alt_text}]({image_map[filename]})"

        return match.group(0)

    return re.sub(pattern, replace_link, markdown_content)


def compute_content_hash(markdown: str) -> str:
    """Compute SHA-256 hash of markdown content for dedup."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()
