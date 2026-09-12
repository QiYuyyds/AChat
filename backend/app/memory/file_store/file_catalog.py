"""File catalog — SQLite table tracking memory file paths and modification times.

Stores `memory_catalog(path TEXT PK, st_mtime REAL, bucket TEXT)` in
`<metadata>/catalog.db`. Supports reconcile (full scan), get_changed
(mtime comparison), upsert, and remove operations.

dream_extract consumes `get_changed()` to process only modified files,
avoiding full scans on every dream cycle.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_catalog (
    path TEXT PRIMARY KEY,
    st_mtime REAL NOT NULL DEFAULT 0,
    bucket TEXT NOT NULL DEFAULT 'daily'
);
"""

_RECREATE_TABLE_SQL = """
DROP TABLE IF EXISTS memory_catalog;
"""


class FileCatalog:
    """SQLite-backed file catalog tracking memory file paths and mtimes."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _bucket_for_path(self, abs_path: Path, daily_dir: Path, digest_dir: Path) -> str:
        try:
            rel = abs_path.relative_to(daily_dir)
            return "daily"
        except ValueError:
            pass
        try:
            rel = abs_path.relative_to(digest_dir)
            parts = rel.parts
            if parts and parts[0] in ("procedure", "personal", "wiki"):
                return parts[0]
            return "digest"
        except ValueError:
            return "daily"

    def reconcile(self, daily_dir: Path, digest_dir: Path) -> int:
        """Full scan of daily/ and digest/ directories.

        Adds new files, removes deleted files, updates stale mtimes.
        Returns the number of files in the catalog after reconcile.
        """
        if not self._conn:
            return 0

        # Collect current files on disk
        disk_files: dict[str, tuple[float, str]] = {}
        for base_dir, _default_bucket in [(daily_dir, "daily"), (digest_dir, "digest")]:
            if not base_dir.exists():
                continue
            for f in base_dir.rglob("*.md"):
                path_str = str(f)
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                bucket = self._bucket_for_path(f, daily_dir, digest_dir)
                disk_files[path_str] = (mtime, bucket)

        # Remove catalog entries not on disk
        cursor = self._conn.execute("SELECT path FROM memory_catalog")
        cataloged = {row[0] for row in cursor.fetchall()}
        deleted = cataloged - set(disk_files.keys())
        if deleted:
            placeholders = ",".join("?" * len(deleted))
            self._conn.execute(
                f"DELETE FROM memory_catalog WHERE path IN ({placeholders})",
                list(deleted),
            )

        # Upsert all disk files, but preserve pending markers (mtime=0):
        # If a catalog entry exists with mtime=0 (pending), keep it at 0 so
        # that get_changed() will still pick it up after reconcile.
        for path_str, (mtime, bucket) in disk_files.items():
            # Check if existing record is pending (mtime=0)
            existing = self._conn.execute(
                "SELECT st_mtime FROM memory_catalog WHERE path = ?", (path_str,)
            ).fetchone()
            if existing is not None and existing[0] == 0.0:
                # Preserve pending marker — do not overwrite with real mtime
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_catalog (path, st_mtime, bucket) VALUES (?, ?, ?)",
                (path_str, mtime, bucket),
            )

        self._conn.commit()
        count = self.count()
        logger.info("FileCatalog reconcile: %d files (%d removed)", count, len(deleted))
        return count

    def get_changed(self, bucket: str | None = None) -> list[tuple[str, str]]:
        """Return (path, bucket) tuples for files whose mtime differs from catalog.

        Also detects deleted files (catalog has entry, file missing) and removes them.
        """
        if not self._conn:
            return []

        if bucket:
            cursor = self._conn.execute(
                "SELECT path, st_mtime, bucket FROM memory_catalog WHERE bucket = ?",
                (bucket,),
            )
        else:
            cursor = self._conn.execute(
                "SELECT path, st_mtime, bucket FROM memory_catalog"
            )

        changed: list[tuple[str, str]] = []
        to_remove: list[str] = []

        for row in cursor.fetchall():
            path_str, cat_mtime, cat_bucket = row
            p = Path(path_str)
            if not p.exists():
                to_remove.append(path_str)
                continue
            try:
                actual_mtime = p.stat().st_mtime
            except OSError:
                to_remove.append(path_str)
                continue
            if actual_mtime != cat_mtime:
                changed.append((path_str, cat_bucket))
                # Update catalog with new mtime
                self._conn.execute(
                    "UPDATE memory_catalog SET st_mtime = ? WHERE path = ?",
                    (actual_mtime, path_str),
                )

        for path_str in to_remove:
            self._conn.execute(
                "DELETE FROM memory_catalog WHERE path = ?", (path_str,)
            )

        if changed or to_remove:
            self._conn.commit()

        logger.info(
            "FileCatalog get_changed: %d changed, %d deleted",
            len(changed), len(to_remove),
        )
        return changed

    def count_changed(self, bucket: str | None = None) -> int:
        """Return the number of files whose mtime differs from catalog.

        Read-only: does NOT update mtime, does NOT delete records. Safe for
        use as a threshold check (unlike get_changed which consumes changes).
        Pending records (mtime=0) are always counted as changed since their
        real mtime will never be 0.
        """
        if not self._conn:
            return 0

        if bucket:
            cursor = self._conn.execute(
                "SELECT path, st_mtime FROM memory_catalog WHERE bucket = ?",
                (bucket,),
            )
        else:
            cursor = self._conn.execute(
                "SELECT path, st_mtime FROM memory_catalog"
            )

        count = 0
        for row in cursor.fetchall():
            path_str, cat_mtime = row
            # Pending records (mtime=0) always count as changed
            if cat_mtime == 0.0:
                count += 1
                continue
            p = Path(path_str)
            if not p.exists():
                # File missing — counts as changed (will be cleaned by get_changed later)
                count += 1
                continue
            try:
                actual_mtime = p.stat().st_mtime
            except OSError:
                count += 1
                continue
            if actual_mtime != cat_mtime:
                count += 1

        return count

    def upsert(self, path: str, st_mtime: float | None = None, bucket: str = "daily") -> None:
        """Insert or update a catalog entry. If st_mtime is None, use current file mtime."""
        if not self._conn:
            return
        if st_mtime is None:
            try:
                st_mtime = Path(path).stat().st_mtime
            except OSError:
                return
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_catalog (path, st_mtime, bucket) VALUES (?, ?, ?)",
            (path, st_mtime, bucket),
        )
        self._conn.commit()

    def remove(self, path: str) -> None:
        """Remove a catalog entry."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM memory_catalog WHERE path = ?", (path,))
        self._conn.commit()

    def count(self) -> int:
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory_catalog")
        return cursor.fetchone()[0]
