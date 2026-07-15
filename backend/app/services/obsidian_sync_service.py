"""ObsidianSyncService — Incremental vault sync engine.

Scan vault → diff against DB records → process added/updated/deleted → report.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

import pathspec

from app.rag.obsidian_preprocessor import ObsidianPreprocessor
from app.services.document_service import DocumentService

logger = logging.getLogger(__name__)

# Default ignore patterns (always excluded unless user overrides)
_DEFAULT_IGNORE_PATTERNS = [
    ".obsidian/",
    "Templates/",
    ".obsidianignore",
]


def _now() -> float:
    return time.time()


def compute_content_hash(content: str) -> str:
    """Compute sha256(content)[:16] for incremental sync diffing."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def load_ignore_spec(vault_path: str | Path) -> pathspec.PathSpec:
    """Load .obsidianignore from vault root, merging with default exclusions.

    Default excludes (.obsidian/, Templates/) are always applied — user
    patterns are merged (union) with them.
    """
    vault = Path(vault_path)
    ignore_file = vault / ".obsidianignore"

    patterns = list(_DEFAULT_IGNORE_PATTERNS)

    if ignore_file.is_file():
        try:
            text = ignore_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        except Exception as e:
            logger.warning("Failed to read .obsidianignore: %s", e)

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_ignored(relative_path: str, spec: pathspec.PathSpec) -> bool:
    """Check if a relative path matches the ignore spec."""
    return spec.match_file(relative_path)


class ObsidianSyncService:
    """Incremental Obsidian vault sync engine.

    Injected with DocumentService and settings via main.py lifespan.
    """

    def __init__(self, document_service: DocumentService) -> None:
        self._doc_svc = document_service
        self._preprocessor = ObsidianPreprocessor()
        self._last_sync: dict[str, dict[str, Any]] = {}

    # ─── Scan vault ────────────────────────────────────────────────────────

    async def scan_vault(
        self, vault_path: str | Path, ignore_spec: pathspec.PathSpec
    ) -> list[tuple[str, str]]:
        """Recursively scan vault for .md files, returning [(relative_path, content_hash)]."""
        vault = Path(vault_path)
        results: list[tuple[str, str]] = []

        for md_file in vault.rglob("*.md"):
            rel = str(md_file.relative_to(vault)).replace("\\", "/")
            if is_ignored(rel, ignore_spec):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                content_hash = compute_content_hash(content)
                results.append((rel, content_hash))
            except Exception as e:
                logger.warning("Failed to read %s: %s", rel, e)

        return results

    # ─── Diff against DB ──────────────────────────────────────────────────

    async def _diff_files(
        self,
        scanned: list[tuple[str, str]],
        db_records: list[dict],
    ) -> dict[str, Any]:
        """Compare scanned files with DB records. Returns {added, updated, deleted, skipped}."""
        scanned_map = {rel: ch for rel, ch in scanned}
        db_map = {r["source_path"]: r for r in db_records if r.get("source_path")}

        added = []
        updated = []
        skipped = []

        for rel, ch in scanned_map.items():
            if rel not in db_map:
                added.append(rel)
            elif db_map[rel].get("content_hash") != ch:
                updated.append((rel, db_map[rel]["id"]))
            else:
                skipped.append(rel)

        deleted = []
        for rel, rec in db_map.items():
            if rel not in scanned_map:
                deleted.append((rel, rec["id"]))

        return {"added": added, "updated": updated, "deleted": deleted, "skipped": skipped}

    # ─── Process individual files ──────────────────────────────────────────

    async def _process_added(
        self,
        relative_path: str,
        vault_path: str | Path,
        user_id: str,
        errors: list[dict[str, str]],
    ) -> bool:
        """Process a newly added vault file."""
        vault = Path(vault_path)
        file_path = vault / relative_path
        try:
            raw = file_path.read_text(encoding="utf-8")
            content_hash = compute_content_hash(raw)
            processed, metadata = self._preprocessor.process(raw, vault_path)

            title = metadata.get("title") or Path(relative_path).stem
            await self._doc_svc.write_document(
                title=title,
                doc_type="note",
                source="obsidian_sync",
                created_by="obsidian_sync",
                content_md=processed,
                metadata=metadata,
                ingest_to_rag=True,
                user_id=user_id,
                source_path=relative_path,
                content_hash=content_hash,
            )
            return True
        except Exception as e:
            logger.warning("Failed to process added file %s: %s", relative_path, e)
            errors.append({"path": relative_path, "error": str(e)})
            return False

    async def _process_updated(
        self,
        relative_path: str,
        document_id: str,
        vault_path: str | Path,
        user_id: str,
        errors: list[dict[str, str]],
    ) -> bool:
        """Process an updated vault file."""
        vault = Path(vault_path)
        file_path = vault / relative_path
        try:
            raw = file_path.read_text(encoding="utf-8")
            content_hash = compute_content_hash(raw)
            processed, metadata = self._preprocessor.process(raw, vault_path)

            title = metadata.get("title") or Path(relative_path).stem
            await self._doc_svc.write_document(
                document_id=document_id,
                title=title,
                doc_type="note",
                source="obsidian_sync",
                created_by="obsidian_sync",
                content_md=processed,
                metadata=metadata,
                ingest_to_rag=True,
                user_id=user_id,
                source_path=relative_path,
                content_hash=content_hash,
            )
            return True
        except Exception as e:
            logger.warning("Failed to process updated file %s: %s", relative_path, e)
            errors.append({"path": relative_path, "error": str(e)})
            return False

    async def _process_deleted(
        self,
        document_id: str,
        errors: list[dict[str, str]],
    ) -> bool:
        """Process a deleted vault file (soft-delete document + clean RAG chunks)."""
        try:
            await self._doc_svc.delete_document(document_id)
            return True
        except Exception as e:
            logger.warning("Failed to delete document %s: %s", document_id, e)
            errors.append({"path": document_id, "error": str(e)})
            return False

    # ─── Main sync method ─────────────────────────────────────────────────

    async def sync_vault(self, vault_path: str | Path, user_id: str) -> dict[str, Any]:
        """Full sync: scan → diff → process → report."""
        vault = Path(vault_path)
        if not vault.is_dir():
            return {
                "scanned": 0,
                "added": 0,
                "updated": 0,
                "deleted": 0,
                "skipped": 0,
                "errors": [{"path": str(vault_path), "error": "Vault path does not exist"}],
            }

        ignore_spec = load_ignore_spec(vault)
        scanned = await self.scan_vault(vault, ignore_spec)

        # Get existing DB records for this user's obsidian_sync documents
        all_docs = await self._doc_svc.list_documents()
        db_records = [
            d for d in all_docs
            if d.get("source") == "obsidian_sync"
            and d.get("status") != "deleted"
            and d.get("user_id") == user_id
        ]

        diff = await self._diff_files(scanned, db_records)
        errors: list[dict[str, str]] = []

        added_count = 0
        for rel in diff["added"]:
            if await self._process_added(rel, vault_path, user_id, errors):
                added_count += 1

        updated_count = 0
        for rel, doc_id in diff["updated"]:
            if await self._process_updated(rel, doc_id, vault_path, user_id, errors):
                updated_count += 1

        deleted_count = 0
        for _rel, doc_id in diff["deleted"]:
            if await self._process_deleted(doc_id, errors):
                deleted_count += 1

        report = {
            "scanned": len(scanned),
            "added": added_count,
            "updated": updated_count,
            "deleted": deleted_count,
            "skipped": len(diff["skipped"]),
            "errors": errors,
        }

        # Cache last sync info
        self._last_sync[user_id] = {
            "timestamp": _now(),
            "summary": report,
        }

        return report

    # ─── Status ────────────────────────────────────────────────────────────

    async def get_status(self, user_id: str, vault_path: str | None) -> dict[str, Any]:
        """Return current vault status and last sync info."""
        vault_exists = False
        total_md_files = 0

        if vault_path:
            vault = Path(vault_path)
            vault_exists = vault.is_dir()
            if vault_exists:
                ignore_spec = load_ignore_spec(vault)
                for md_file in vault.rglob("*.md"):
                    rel = str(md_file.relative_to(vault)).replace("\\", "/")
                    if not is_ignored(rel, ignore_spec):
                        total_md_files += 1

        last_sync = self._last_sync.get(user_id)

        return {
            "vault_path": vault_path,
            "vault_exists": vault_exists,
            "total_md_files": total_md_files,
            "last_sync_at": last_sync["timestamp"] if last_sync else None,
            "last_sync_summary": last_sync["summary"] if last_sync else None,
        }
