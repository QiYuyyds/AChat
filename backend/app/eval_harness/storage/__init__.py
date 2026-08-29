"""
Storage implementations for the Aeval evaluation framework.

Provides SQLite (default) and in-memory storage.
PostgreSQL can be added for production use.

Usage:
    from eval_harness.storage import SqliteStorage, MemoryStorage

    storage = SqliteStorage(db_path="./aeval.db")
    await storage.save_run(run_result)
"""

from eval_harness.storage.memory import MemoryStorage
from eval_harness.storage.sqlite import SqliteStorage

__all__ = ["MemoryStorage", "SqliteStorage"]
