"""Tests for the document-version-refresh change.

Covers:
- 7.1: pdftotext page count (\\x0c form-feed counting)
- 7.2: delete_versions_by_document cleans PG/ES/Milvus/KG four ways
- 7.3: upload_file with document_id creates new version (version+1)
- 7.4: chunk content_hash cache hit skips embed_fn call
"""

from __future__ import annotations

import hashlib
import time

# ─── 7.1: pdftotext page count ─────────────────────────────────────────────


def test_pdftotext_page_count(monkeypatch):
    """pdftotext fallback should count \\x0c form feeds for page count."""
    import app.rag.parser as parser_mod

    processor = parser_mod.DocumentProcessor()

    # Mock pdftotext binary availability
    monkeypatch.setattr(parser_mod.shutil, "which", lambda cmd: "/usr/bin/pdftotext")

    # Mock subprocess output: 2 form feeds = 3 pages
    fake_output = b"Page 1 text\x0cPage 2 text\x0cPage 3 text"
    monkeypatch.setattr(
        parser_mod.subprocess, "check_output", lambda *a, **kw: fake_output
    )

    text, pages, parser_name, _ = processor._extract_pdf_with_pdftotext(b"fake pdf")
    assert parser_name == "pdftotext"
    assert pages == 3
    assert "Page 1" in text
    assert "Page 3" in text


def test_pdftotext_single_page(monkeypatch):
    """pdftotext with no form feed should report 1 page."""
    import app.rag.parser as parser_mod

    processor = parser_mod.DocumentProcessor()

    monkeypatch.setattr(parser_mod.shutil, "which", lambda cmd: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        parser_mod.subprocess,
        "check_output",
        lambda *a, **kw: b"single page content",
    )

    _, pages, parser_name, _ = processor._extract_pdf_with_pdftotext(b"fake pdf")
    assert parser_name == "pdftotext"
    assert pages == 1


def test_pdftotext_empty_output_zero_pages(monkeypatch):
    """pdftotext with empty output should report 0 pages."""
    import app.rag.parser as parser_mod

    processor = parser_mod.DocumentProcessor()

    monkeypatch.setattr(parser_mod.shutil, "which", lambda cmd: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        parser_mod.subprocess, "check_output", lambda *a, **kw: b"  \n  "
    )

    _, pages, _, _ = processor._extract_pdf_with_pdftotext(b"fake pdf")
    assert pages == 0


# ─── 7.2: delete_versions_by_document cleans four ways ─────────────────────


async def test_delete_versions_by_document_cleans_four_ways(db):
    """delete_versions_by_document should clean PG + ES + Milvus + KG."""
    from app.config import get_settings
    from app.db.engine import get_db
    from app.db.models import Document, DocumentVersion, RagChunk
    from app.services.document_service import DocumentService
    from app.services.rag_service import RAGService
    from app.utils.ids import new_document_id, new_document_version_id

    settings = get_settings()
    rag_svc = RAGService(settings)

    # Track mock callback invocations
    es_calls: list[list[int]] = []
    milvus_calls: list[list[int]] = []
    kg_calls: list[str] = []

    async def mock_es_delete(pg_ids):
        es_calls.append(list(pg_ids))

    def mock_milvus_delete(pg_ids):
        milvus_calls.append(list(pg_ids))

    async def mock_kg_delete(doc_hash):
        kg_calls.append(doc_hash)

    rag_svc.set_es_delete_fn(mock_es_delete)
    rag_svc.set_milvus_delete_fn(mock_milvus_delete)
    rag_svc.set_kg_delete_fn(mock_kg_delete)

    # Create a document + version + rag_chunks
    doc_id = new_document_id()
    ver_id = new_document_version_id()
    now = time.time()

    async with get_db() as session:
        doc = Document(
            id=doc_id,
            title="Test Doc",
            doc_type="note",
            source="user_upload",
            status="active",
            created_by="user",
            created_at=now,
            updated_at=now,
            latest_version=1,
            latest_version_id=ver_id,
        )
        session.add(doc)
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version=1,
            content_md="some content",
            created_at=now,
        )
        session.add(ver)
        await session.flush()  # Ensure doc + ver exist before adding chunks (FK)
        # Add two rag_chunks for this document with different doc_hashes
        chunk1 = RagChunk(
            doc_hash="hash_aaa",
            chunk_idx=0,
            content="chunk one",
            embedding=[0.1] * 10,
            created_at=now,
            document_id=doc_id,
            version_id=ver_id,
            content_hash="ch_001",
        )
        chunk2 = RagChunk(
            doc_hash="hash_bbb",
            chunk_idx=1,
            content="chunk two",
            embedding=[0.2] * 10,
            created_at=now,
            document_id=doc_id,
            version_id=ver_id,
            content_hash="ch_002",
        )
        session.add(chunk1)
        session.add(chunk2)

    # Call delete_versions_by_document through DocumentService
    svc = DocumentService(db=None, rag=rag_svc)
    deleted = await svc.delete_versions_by_document(doc_id)

    # PG rows deleted
    assert deleted == 2

    # ES callback called with pg_ids
    assert len(es_calls) == 1
    assert len(es_calls[0]) == 2

    # Milvus callback called with pg_ids
    assert len(milvus_calls) == 1
    assert len(milvus_calls[0]) == 2

    # KG callback called for each unique doc_hash
    assert set(kg_calls) == {"hash_aaa", "hash_bbb"}
    assert len(kg_calls) == 2

    # Verify PG has no more chunks for this document
    from sqlalchemy import func, select

    async with get_db() as session:
        count_result = await session.execute(
            select(func.count()).select_from(RagChunk).where(
                RagChunk.document_id == doc_id
            )
        )
        remaining = count_result.scalar() or 0
        assert remaining == 0


async def test_delete_versions_by_document_empty_is_noop(db):
    """Deleting RAG data for a document with no chunks should return 0, no error."""
    from app.config import get_settings
    from app.services.document_service import DocumentService
    from app.services.rag_service import RAGService

    settings = get_settings()
    rag_svc = RAGService(settings)

    es_calls: list = []
    rag_svc.set_es_delete_fn(lambda ids: es_calls.append(ids))

    svc = DocumentService(db=None, rag=rag_svc)
    deleted = await svc.delete_versions_by_document("doc_nonexistent")
    assert deleted == 0
    assert len(es_calls) == 0


# ─── 7.3: upload_file with document_id creates new version ─────────────────


async def test_upload_file_with_document_id_creates_new_version(db):
    """upload_file with document_id should create a new version (version+1)."""
    from app.services.document_service import DocumentService

    svc = DocumentService(db=None, rag=None)

    # First upload creates a new document (v1)
    content1 = b"# First version\nThis is the initial content."
    result1 = await svc.upload_file("test.md", "text/markdown", content1)
    assert result1["success"] is True
    assert result1["version"]["version"] == 1
    doc_id = result1["document"]["id"]

    # Second upload with document_id creates a new version (v2)
    content2 = b"# Second version\nThis is updated content."
    result2 = await svc.upload_file(
        "test_v2.md", "text/markdown", content2, document_id=doc_id
    )
    assert result2["success"] is True
    assert result2["document"]["id"] == doc_id  # Same document
    assert result2["version"]["version"] == 2  # New version
    assert result2["document"]["latest_version"] == 2

    # Third upload with document_id + title override
    content3 = b"# Third version\nMore updates."
    result3 = await svc.upload_file(
        "test_v3.md",
        "text/markdown",
        content3,
        document_id=doc_id,
        title="Custom Title",
        doc_type="manual",
    )
    assert result3["success"] is True
    assert result3["document"]["id"] == doc_id
    assert result3["version"]["version"] == 3
    assert result3["document"]["title"] == "Custom Title"
    assert result3["document"]["doc_type"] == "manual"


async def test_upload_file_without_document_id_creates_new_doc(db):
    """upload_file without document_id should create a new document each time."""
    from app.services.document_service import DocumentService

    svc = DocumentService(db=None, rag=None)

    result1 = await svc.upload_file("a.md", "text/markdown", b"content A")
    result2 = await svc.upload_file("b.md", "text/markdown", b"content B")

    assert result1["document"]["id"] != result2["document"]["id"]
    assert result1["version"]["version"] == 1
    assert result2["version"]["version"] == 1


# ─── 7.4: chunk content_hash cache hit skips embed_fn ──────────────────────


async def test_chunk_content_hash_hit_skips_embed_fn(db):
    """When content_hash matches an existing chunk with embedding, embed_fn is skipped."""
    from app.config import get_settings
    from app.db.engine import get_db
    from app.db.models import RagChunk
    from app.rag.rag_engine import RAGEngine

    settings = get_settings()

    # Use a short content that will be a single chunk (< rag_chunk_size=200)
    chunk_content = "This is a short test content for cache hit verification."
    content_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()[:16]
    expected_dim = settings.rag_milvus_dim  # 1024
    fake_embedding = [0.15] * expected_dim

    # Pre-seed a rag_chunk with this content_hash and embedding
    async with get_db() as session:
        row = RagChunk(
            doc_hash="preexisting_doc_hash",
            chunk_idx=0,
            content=chunk_content,
            embedding=fake_embedding,
            created_at=time.time(),
            content_hash=content_hash,
        )
        session.add(row)

    # Create RAGEngine with mock embed_fn (no hybrid store)
    embed_calls: list[str] = []

    def mock_embed(text: str):
        embed_calls.append(text)
        return [0.99] * expected_dim

    engine = RAGEngine(settings, hybrid=None)
    engine.set_embed_fn(mock_embed)

    # Ingest the same content — should hit cache
    count = await engine.ingest(chunk_content)

    # Should have produced 1 chunk
    assert count == 1
    # embed_fn should NOT have been called (cache hit)
    assert len(embed_calls) == 0


async def test_chunk_content_hash_miss_calls_embed_fn(db):
    """When content_hash does not match, embed_fn should be called normally."""
    from app.config import get_settings
    from app.db.engine import get_db
    from app.db.models import RagChunk
    from app.rag.rag_engine import RAGEngine

    settings = get_settings()

    # Pre-seed a chunk with a DIFFERENT content
    other_content = "completely different content that won't match"
    other_hash = hashlib.sha256(other_content.encode("utf-8")).hexdigest()[:16]
    expected_dim = settings.rag_milvus_dim

    async with get_db() as session:
        row = RagChunk(
            doc_hash="other_hash",
            chunk_idx=0,
            content=other_content,
            embedding=[0.3] * expected_dim,
            created_at=time.time(),
            content_hash=other_hash,
        )
        session.add(row)

    # New content that won't match the pre-seeded hash
    new_content = "This is brand new content not in the cache."
    embed_calls: list[str] = []

    def mock_embed(text: str):
        embed_calls.append(text)
        return [0.88] * expected_dim

    engine = RAGEngine(settings, hybrid=None)
    engine.set_embed_fn(mock_embed)

    count = await engine.ingest(new_content)
    assert count == 1
    # embed_fn SHOULD have been called (cache miss)
    assert len(embed_calls) == 1


async def test_chunk_dim_mismatch_treated_as_miss(db):
    """When cached embedding dimension doesn't match, embed_fn is called."""
    from app.config import get_settings
    from app.db.engine import get_db
    from app.db.models import RagChunk
    from app.rag.rag_engine import RAGEngine

    settings = get_settings()
    expected_dim = settings.rag_milvus_dim  # 1024

    chunk_content = "content for dimension mismatch test"
    content_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()[:16]

    # Pre-seed with WRONG dimension embedding
    async with get_db() as session:
        row = RagChunk(
            doc_hash="mismatch_hash",
            chunk_idx=0,
            content=chunk_content,
            embedding=[0.1] * 512,  # Wrong dim (512 != 1024)
            created_at=time.time(),
            content_hash=content_hash,
        )
        session.add(row)

    embed_calls: list[str] = []

    def mock_embed(text: str):
        embed_calls.append(text)
        return [0.77] * expected_dim

    engine = RAGEngine(settings, hybrid=None)
    engine.set_embed_fn(mock_embed)

    count = await engine.ingest(chunk_content)
    assert count == 1
    # embed_fn SHOULD have been called (dim mismatch → treated as miss)
    assert len(embed_calls) == 1
