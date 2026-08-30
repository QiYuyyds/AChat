"""Dataset construction and management for the Aeval evaluation framework.

Modules:
    models  — EvalDataset / EvalDatasetItem with provenance + to_suite()
    storage — DatasetStorage protocol + SQLite / Memory implementations
    quality — DatasetQualityChecker + CoverageAnalyzer
    version — DatasetVersionManager (semver bump + change log)
    sources — manual import / trace mining / LLM generation / regression extract
"""

from agent_eval.dataset.models import (
    DatasetError,
    EvalDataset,
    EvalDatasetItem,
    SourceType,
)
from agent_eval.dataset.storage import (
    DatasetStorage,
    MemoryDatasetStorage,
    SqliteDatasetStorage,
)

__all__ = [
    "DatasetError",
    "EvalDataset",
    "EvalDatasetItem",
    "SourceType",
    "DatasetStorage",
    "MemoryDatasetStorage",
    "SqliteDatasetStorage",
]
