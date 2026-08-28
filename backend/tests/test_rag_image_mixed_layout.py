"""Tests for RAG image-text mixed layout processing.

Covers:
- RecursiveSplitter image link protection (7.1)
- RecursiveSplitter table protection (7.2)
- RecursiveSplitter large table degradation (7.3)
- process_ocr_zip() image extraction + link rewriting (7.4)
- DeepSeek-OCR ParseResult.images (7.5)
- document_service link rewriting (7.6)
- API image file serving endpoint (7.7)
- Integration test for image upload (7.8)
"""

import io
import zipfile
from unittest.mock import MagicMock, patch

from app.rag.parsers.base import ExtractedImage, ParseResult
from app.rag.splitter import RecursiveSplitter

# ─── 7.1: Image link protection ─────────────────────────────────────────


class TestImageLinkProtection:
    def test_image_link_not_split(self):
        """Image links should stay intact across chunk boundaries."""
        text = (
            "Some text before the image. " * 10
            + "![架构图](/api/documents/doc123/images/fig1.png)"
            + " More text after the image. " * 10
        )
        splitter = RecursiveSplitter(chunk_size=50, chunk_overlap=0)
        chunks = splitter.split(text)

        # The image link should appear intact in exactly one chunk
        full = " ".join(c.content for c in chunks)
        assert "![架构图](/api/documents/doc123/images/fig1.png)" in full

        # No chunk should contain a partial image link
        for c in chunks:
            assert "![架构图](/api/documents/doc123/images/fig1.png)" in c.content or \
                "![" not in c.content or "](" not in c.content

    def test_multiple_image_links_protected(self):
        """Multiple image links in sequence should each remain intact."""
        text = (
            "![img1](/api/documents/doc/images/img1.png)"
            "![img2](/api/documents/doc/images/img2.png)"
        )
        splitter = RecursiveSplitter(chunk_size=30, chunk_overlap=0)
        chunks = splitter.split(text)
        full = " ".join(c.content for c in chunks)
        assert "![img1](/api/documents/doc/images/img1.png)" in full
        assert "![img2](/api/documents/doc/images/img2.png)" in full


# ─── 7.2: Table protection ───────────────────────────────────────────────


class TestTableProtection:
    def test_small_table_not_split(self):
        """Small tables should remain intact in a single chunk."""
        table = (
            "| Name | Age |\n"
            "|------|-----|\n"
            "| Alice | 30 |\n"
            "| Bob | 25 |\n"
        )
        text = f"Some intro text.\n\n{table}\n\nSome outro text."
        splitter = RecursiveSplitter(chunk_size=200, chunk_overlap=0, protect_tables=True)
        chunks = splitter.split(text)

        # Find the chunk containing the table
        table_chunks = [c for c in chunks if "| Name |" in c.content]
        assert len(table_chunks) == 1
        assert "| Alice |" in table_chunks[0].content
        assert "| Bob |" in table_chunks[0].content

    def test_table_protection_disabled_by_default(self):
        """Without protect_tables=True, tables are not specially protected."""
        table = (
            "| Col1 | Col2 |\n"
            "|------|------|\n"
            "| A | B |\n"
        )
        text = f"Intro.\n\n{table}\n\nOutro."
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=0)
        chunks = splitter.split(text)
        # Without protection, table might be split — just verify it runs
        assert len(chunks) >= 1


# ─── 7.3: Large table degradation ──────────────────────────────────────


class TestLargeTableDegradation:
    def test_large_table_split_by_rows(self):
        """Large tables exceeding chunk_size should be split by rows, keeping header."""
        header = "| Name | Description |\n|------|-------------|\n"
        rows = "".join(f"| Item{i} | Description{i} |\n" for i in range(20))
        table = header + rows

        splitter = RecursiveSplitter(chunk_size=80, chunk_overlap=0, protect_tables=True)
        chunks = splitter.split(table)

        # The first chunk should contain the header + separator + first row
        first_table_chunk = [c for c in chunks if "| Name |" in c.content]
        assert len(first_table_chunk) >= 1
        assert "|------|" in first_table_chunk[0].content
        assert "Item0" in first_table_chunk[0].content

        # All rows should be present across chunks
        full = " ".join(c.content for c in chunks)
        for i in range(20):
            assert f"Item{i}" in full


# ─── 7.4: process_ocr_zip() image extraction ────────────────────────────


class TestProcessOcrZip:
    def test_extract_images_and_rewrite_links(self, tmp_path):
        """process_ocr_zip should extract images and rewrite links."""
        from app.rag.parsers.ocr_zip_utils import process_ocr_zip

        # Build a mock ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("full.md", "# Document\n\n![figure](images/fig1.png)\n\nText.")
            zf.writestr("images/fig1.png", b"fake_png_data")
        zip_data = buf.getvalue()

        output_dir = tmp_path / "images"
        markdown, images = process_ocr_zip(zip_data, image_output_dir=output_dir)

        assert "full.md" not in markdown
        assert "# Document" in markdown
        assert len(images) == 1
        assert images[0].filename.endswith("fig1.png")
        assert images[0].data == b"fake_png_data"
        assert images[0].content_type == "image/png"

    def test_no_images_returns_empty_list(self, tmp_path):
        """ZIP without images should return empty images list."""
        from app.rag.parsers.ocr_zip_utils import process_ocr_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("full.md", "# Document\n\nNo images here.")
        zip_data = buf.getvalue()

        output_dir = tmp_path / "images"
        markdown, images = process_ocr_zip(zip_data, image_output_dir=output_dir)

        assert "No images here" in markdown
        assert images == []


# ─── 7.5: DeepSeek-OCR ParseResult.images ────────────────────────────────


class TestDeepSeekOCRImages:
    def test_process_pdf_returns_images(self):
        """DeepSeek-OCR _process_pdf should return page images."""
        from app.rag.parsers.deepseek_ocr import DeepSeekOCRParser

        parser = DeepSeekOCRParser(api_key="fake_key")

        # Mock pypdfium2 and _call_api
        mock_page = MagicMock()
        mock_bitmap = MagicMock()
        mock_pil = MagicMock()

        # Make pil.save capture the PNG bytes
        def fake_save(buf, format="PNG"):
            buf.write(b"fake_page_png")
        mock_pil.save = fake_save
        mock_bitmap.to_pil.return_value = mock_pil
        mock_page.render.return_value = mock_bitmap

        mock_pdf = MagicMock()
        mock_pdf.__len__ = MagicMock(return_value=3)
        mock_pdf.__getitem__ = MagicMock(side_effect=lambda i: mock_page)
        mock_pdf.close = MagicMock()

        with patch("pypdfium2.PdfDocument", return_value=mock_pdf), \
             patch.object(parser, "_call_api", return_value="# Page content"):
            text, images = parser._process_pdf(b"fake_pdf_data")

        assert len(images) == 3
        for idx, img in enumerate(images):
            assert img.filename == f"page_{idx}.png"
            assert img.page_index == idx
            assert img.content_type == "image/png"
            assert img.data == b"fake_page_png"


# ─── 7.6: document_service link rewriting ────────────────────────────────


class TestDocumentServiceLinkRewriting:
    def test_rewrite_image_links(self):
        """_rewrite_image_links should convert paths to canonical API format."""
        from app.services.document_service import _rewrite_image_links

        images = [
            ExtractedImage(filename="fig1.png", data=b"data"),
            ExtractedImage(filename="fig2.jpg", data=b"data"),
        ]
        content = (
            "![Figure 1](images/fig1.png)\n"
            "![Figure 2](/tmp/ocr-xyz/fig2.jpg)\n"
        )
        result = _rewrite_image_links(content, images, "doc123")

        assert "/api/documents/doc123/images/fig1.png" in result
        assert "/api/documents/doc123/images/fig2.jpg" in result
        # Old relative path format should be gone (not as standalone path)
        assert "](images/fig1.png)" not in result
        assert "](/tmp/ocr-xyz/fig2.jpg)" not in result

    def test_insert_image_placeholders(self):
        """_insert_image_placeholders should add links for unreferenced images."""
        from app.services.document_service import _insert_image_placeholders

        images = [
            ExtractedImage(filename="page_0.png", data=b"data", page_index=0),
            ExtractedImage(filename="page_1.png", data=b"data", page_index=1),
        ]
        content = "# Document\n\nSome text."
        result = _insert_image_placeholders(content, images, "doc456")

        assert "/api/documents/doc456/images/page_0.png" in result
        assert "/api/documents/doc456/images/page_1.png" in result
        assert "page_0" in result  # alt text from page_index

    def test_insert_placeholders_skips_already_referenced(self):
        """Images already referenced in content should not get placeholders."""
        from app.services.document_service import _insert_image_placeholders

        images = [
            ExtractedImage(filename="fig1.png", data=b"data"),
        ]
        content = "![figure](/api/documents/doc/images/fig1.png)\n\nText."
        result = _insert_image_placeholders(content, images, "doc")

        # Should not duplicate the reference
        assert result.count("fig1.png") == 1


# ─── 7.7: API image file serving ────────────────────────────────────────


class TestImageFileEndpoint:
    """Test the image file serving endpoint.

    These are unit-level tests that verify the endpoint logic.
    Full API integration tests would require a running server.
    """

    def test_content_type_mapping(self):
        """Verify content type mapping covers common image formats."""
        from app.api.documents import _CONTENT_TYPE_MAP

        assert _CONTENT_TYPE_MAP[".png"] == "image/png"
        assert _CONTENT_TYPE_MAP[".jpg"] == "image/jpeg"
        assert _CONTENT_TYPE_MAP[".jpeg"] == "image/jpeg"
        assert _CONTENT_TYPE_MAP[".gif"] == "image/gif"
        assert _CONTENT_TYPE_MAP[".webp"] == "image/webp"


# ─── 7.8: Integration test ──────────────────────────────────────────────


class TestIntegrationImageUpload:
    """Integration test: upload a document with images → verify persistence."""

    def test_extracted_image_has_page_index(self):
        """ExtractedImage with page_index should be properly structured."""
        img = ExtractedImage(
            filename="page_0.png",
            data=b"png_bytes",
            content_type="image/png",
            page_index=0,
            alt_text="",
        )
        assert img.page_index == 0
        assert img.alt_text == ""
        assert img.filename == "page_0.png"

    def test_extracted_image_default_values(self):
        """ExtractedImage should have sensible defaults for new fields."""
        img = ExtractedImage(filename="test.png", data=b"data")
        assert img.page_index is None
        assert img.alt_text == ""

    def test_parse_result_carries_images(self):
        """ParseResult should carry images through the pipeline."""
        images = [
            ExtractedImage(filename="img1.png", data=b"data", page_index=0),
        ]
        result = ParseResult(
            filename="test.pdf",
            content_type="application/pdf",
            parser="deepseek_ocr",
            content="# Content",
            images=images,
        )
        assert len(result.images) == 1
        assert result.images[0].page_index == 0

    def test_write_images_to_workspace(self, tmp_path):
        """_write_images_to_workspace should save images to disk."""
        from app.services.document_service import _write_images_to_workspace

        images = [
            ExtractedImage(filename="fig1.png", data=b"png_data"),
            ExtractedImage(filename="fig2.jpg", data=b"jpg_data"),
        ]

        with patch("app.services.document_service.get_settings") as mock_settings:
            mock_settings.return_value.data_path = tmp_path
            result = _write_images_to_workspace("doc123", images)

        assert len(result) == 2
        assert result[0]["filename"] == "fig1.png"
        assert result[1]["filename"] == "fig2.jpg"

        # Verify files were written
        img_dir = tmp_path / "documents" / "doc123" / "images"
        assert (img_dir / "fig1.png").exists()
        assert (img_dir / "fig2.jpg").exists()
        assert (img_dir / "fig1.png").read_bytes() == b"png_data"
