"""
SQLite storage implementation.

Default storage backend for single-machine use.
No external services required.

Usage:
    storage = SqliteStorage(db_path="./aeval.db")
    await storage.initialize()
    await storage.save_run(run_result)
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from eval_harness.core.types import EvalSuite, RunResult
from eval_harness.dataset.storage import SqliteDatasetStorage


class SqliteStorage:
    """
    SQLite 存储实现。

    适合单机开发 / 轻量使用。
    无需额外服务, 开箱即用。
    """

    def __init__(self, db_path: str = "./aeval.db"):
        """
        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._initialized = False
        # 数据集存储组合暴露 (同一 db 文件, datasets/dataset_items 分表)
        self.datasets = SqliteDatasetStorage(db_path)

    async def initialize(self) -> None:
        """初始化数据库表结构"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    suite_name TEXT,
                    status TEXT,
                    started_at REAL,
                    completed_at REAL,
                    data TEXT
                );

                CREATE TABLE IF NOT EXISTS suites (
                    name TEXT PRIMARY KEY,
                    data TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS human_score_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    task_id TEXT,
                    trial_index INTEGER,
                    grader_name TEXT,
                    data TEXT,
                    created_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_suite
                    ON runs(suite_name);
                CREATE INDEX IF NOT EXISTS idx_runs_status
                    ON runs(status);
                CREATE INDEX IF NOT EXISTS idx_runs_started
                    ON runs(started_at);
                CREATE INDEX IF NOT EXISTS idx_human_requests_run
                    ON human_score_requests(run_id);
            """)
        await self.datasets.initialize()
        self._initialized = True

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "SqliteStorage not initialized. Call await storage.initialize() first."
            )

    # ── Run 操作 ──

    async def save_run(self, run: RunResult) -> None:
        """保存运行结果"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, suite_name, status, started_at, completed_at, data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    run.run_id,
                    run.suite_name,
                    run.status,
                    run.started_at,
                    run.completed_at,
                    json.dumps(run.model_dump(), default=str),
                ),
            )
            await db.commit()

    async def get_run(self, run_id: str) -> RunResult | None:
        """获取运行结果"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM runs WHERE run_id = ?", (run_id,)
            )
            row = await cursor.fetchone()
            if row:
                data = json.loads(row["data"])
                return RunResult(**data)
            return None

    async def list_runs(
        self, suite_name: str | None = None, limit: int = 50
    ) -> list[RunResult]:
        """列出运行历史"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if suite_name:
                cursor = await db.execute(
                    "SELECT data FROM runs WHERE suite_name = ? ORDER BY started_at DESC LIMIT ?",
                    (suite_name, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT data FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [RunResult(**json.loads(r["data"])) for r in rows]

    async def delete_run(self, run_id: str) -> bool:
        """删除运行结果"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM runs WHERE run_id = ?", (run_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── Suite 操作 ──

    async def save_suite(self, suite: EvalSuite) -> None:
        """保存评测套件"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO suites (name, data, created_at)
                   VALUES (?, ?, ?)""",
                (suite.name, json.dumps(suite.model_dump(), default=str), time.time()),
            )
            await db.commit()

    async def get_suite(self, name: str) -> EvalSuite | None:
        """获取评测套件"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM suites WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()
            if row:
                data = json.loads(row["data"])
                return EvalSuite(**data)
            return None

    async def list_suites(self) -> list[EvalSuite]:
        """列出所有评测套件"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT data FROM suites ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [EvalSuite(**json.loads(r["data"])) for r in rows]

    async def delete_suite(self, name: str) -> bool:
        """删除评测套件"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM suites WHERE name = ?", (name,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── 人工评分请求 ──

    async def save_human_score_request(self, request: dict[str, Any]) -> None:
        """保存人工评分请求 (HumanGrader pending 语义)"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO human_score_requests
                   (run_id, task_id, trial_index, grader_name, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    request.get("run_id", ""),
                    request.get("task_id", ""),
                    request.get("trial_index", 0),
                    request.get("grader_name", "human"),
                    json.dumps(request, default=str),
                    time.time(),
                ),
            )
            await db.commit()

    async def list_human_score_requests(
        self, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出人工评分请求"""
        self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if run_id is not None:
                cursor = await db.execute(
                    "SELECT data FROM human_score_requests WHERE run_id = ? "
                    "ORDER BY id ASC",
                    (run_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT data FROM human_score_requests ORDER BY id ASC"
                )
            rows = await cursor.fetchall()
            return [json.loads(r["data"]) for r in rows]
