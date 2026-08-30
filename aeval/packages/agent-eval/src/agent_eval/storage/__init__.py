"""
Storage implementations for the Aeval evaluation framework.

Provides SQLite (default) and in-memory storage.
PostgreSQL can be added for production use.

Usage:
    from agent_eval.storage import SqliteStorage, MemoryStorage

    storage = SqliteStorage(db_path="./aeval.db")
    await storage.save_run(run_result)
"""

from agent_eval.storage.memory import MemoryStorage
from agent_eval.storage.sqlite import SqliteStorage

__all__ = ["MemoryStorage", "SqliteStorage"]
