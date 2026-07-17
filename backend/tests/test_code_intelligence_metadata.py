import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_metadata_is_validated_and_written_atomically(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import (
        CodeIntelligenceCounts,
        CodeIntelligenceMetadata,
        MetadataStore,
    )

    store = MetadataStore(tmp_path / "internal-workspace")
    metadata = CodeIntelligenceMetadata(
        enabled=True,
        runtime_version="0.9.3",
        status="indexing",
        phase="extracting symbols",
        progress_percent=42,
        counts=CodeIntelligenceCounts(files=12, symbols=340, relationships=88),
        created_at=100,
        updated_at=200,
        started_at=150,
    )

    store.write(metadata)

    assert store.read() == metadata
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["runtimeVersion"] == "0.9.3"
    assert raw["counts"] == {"files": 12, "symbols": 340, "relationships": 88}
    assert raw["createdAt"] == 100
    assert raw["progressPercent"] == 42
    assert not list(store.path.parent.glob("*.tmp-*"))


def test_missing_metadata_defaults_to_disabled(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import MetadataStore

    metadata = MetadataStore(tmp_path).read()

    assert metadata.enabled is False
    assert metadata.status == "disabled"
    assert metadata.counts.files == 0


def test_metadata_bounds_counts_and_error_size() -> None:
    from app.code_intelligence.metadata import CodeIntelligenceCounts, CodeIntelligenceMetadata

    with pytest.raises(ValidationError):
        CodeIntelligenceCounts(files=-1)
    with pytest.raises(ValidationError):
        CodeIntelligenceMetadata(error="x" * 2001)
    with pytest.raises(ValidationError):
        CodeIntelligenceMetadata(progress_percent=100)
