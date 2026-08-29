"""
DatasetStorage — persistence for eval datasets, separate from run/suite storage.

Defines the DatasetStorage protocol plus SQLite and Memory implementations.
The implementations are composed onto the existing Storage instances
(`storage.datasets`) rather than opened as a second connection, so the API
layer reaches them through the same EvalRunner.storage instance.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

import aiosqlite
from eval_harness.dataset.models import EvalDataset, EvalDatasetItem


def _version_key(version: str) -> tuple[int, int, int]:
    """semver 排序键 (非法片段按 0 处理)"""
    parts = []
    for p in version.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


@runtime_checkable
class DatasetStorage(Protocol):
    """数据集存储接口 (datasets / dataset_items)"""

    async def save_dataset(self, dataset: EvalDataset) -> None:
        """保存数据集 (非空 items 同步入条目表)"""
        ...

    async def get_dataset(self, dataset_id: str) -> EvalDataset | None:
        """按 ID 获取数据集 (含条目)"""
        ...

    async def get_dataset_by_name(
        self, name: str, version: str | None = None
    ) -> EvalDataset | None:
        """按名称查询; version 为 None 时返回最高版本"""
        ...

    async def list_datasets(self, tags: list[str] | None = None) -> list[EvalDataset]:
        """列出数据集; tags 非空时按标签过滤 (任一命中)"""
        ...

    async def delete_dataset(self, dataset_id: str) -> bool:
        """删除数据集 (级联删除条目)"""
        ...

    async def save_dataset_item(self, dataset_id: str, item: EvalDatasetItem) -> None:
        """新增或更新条目"""
        ...

    async def get_dataset_items(self, dataset_id: str) -> list[EvalDatasetItem]:
        """列出数据集全部条目"""
        ...

    async def get_dataset_item(
        self, dataset_id: str, item_id: str
    ) -> EvalDatasetItem | None:
        """按 ID 获取单条条目"""
        ...

    async def delete_dataset_item(self, dataset_id: str, item_id: str) -> bool:
        """删除单条条目"""
        ...


def _strip_items(dataset: EvalDataset) -> EvalDataset:
    """数据集主记录不含条目 (条目单独存表)"""
    return dataset.model_copy(update={"items": []})


class MemoryDatasetStorage:
    """内存数据集存储 — 用于测试与短生命周期场景"""

    def __init__(self) -> None:
        self._datasets: dict[str, EvalDataset] = {}
        self._items: dict[str, dict[str, EvalDatasetItem]] = {}

    async def save_dataset(self, dataset: EvalDataset) -> None:
        self._datasets[dataset.id] = _strip_items(dataset)
        bucket = self._items.setdefault(dataset.id, {})
        for item in dataset.items:
            bucket[item.id] = item

    async def get_dataset(self, dataset_id: str) -> EvalDataset | None:
        stored = self._datasets.get(dataset_id)
        if stored is None:
            return None
        items = list(self._items.get(dataset_id, {}).values())
        return stored.model_copy(update={"items": items})

    async def get_dataset_by_name(
        self, name: str, version: str | None = None
    ) -> EvalDataset | None:
        candidates = [d for d in self._datasets.values() if d.name == name]
        if version is not None:
            candidates = [d for d in candidates if d.version == version]
        if not candidates:
            return None
        best = max(candidates, key=lambda d: _version_key(d.version))
        return await self.get_dataset(best.id)

    async def list_datasets(self, tags: list[str] | None = None) -> list[EvalDataset]:
        result = []
        for dataset_id in self._datasets:
            dataset = await self.get_dataset(dataset_id)
            assert dataset is not None
            if tags and not set(tags) & set(dataset.tags):
                continue
            result.append(dataset)
        result.sort(key=lambda d: d.created_at, reverse=True)
        return result

    async def delete_dataset(self, dataset_id: str) -> bool:
        if dataset_id not in self._datasets:
            return False
        del self._datasets[dataset_id]
        self._items.pop(dataset_id, None)
        return True

    async def save_dataset_item(self, dataset_id: str, item: EvalDatasetItem) -> None:
        if dataset_id not in self._datasets:
            raise KeyError(f"Dataset '{dataset_id}' not found")
        self._items.setdefault(dataset_id, {})[item.id] = item

    async def get_dataset_items(self, dataset_id: str) -> list[EvalDatasetItem]:
        return list(self._items.get(dataset_id, {}).values())

    async def get_dataset_item(
        self, dataset_id: str, item_id: str
    ) -> EvalDatasetItem | None:
        return self._items.get(dataset_id, {}).get(item_id)

    async def delete_dataset_item(self, dataset_id: str, item_id: str) -> bool:
        bucket = self._items.get(dataset_id)
        if bucket and item_id in bucket:
            del bucket[item_id]
            return True
        return False


class SqliteDatasetStorage:
    """SQLite 数据集存储 — 与 run/suite 存储共用同一 db 文件"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self) -> None:
        """建表 (由宿主 SqliteStorage.initialize() 调用)"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    version TEXT,
                    data TEXT,
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS dataset_items (
                    dataset_id TEXT,
                    item_id TEXT,
                    data TEXT,
                    created_at REAL,
                    PRIMARY KEY (dataset_id, item_id)
                );

                CREATE INDEX IF NOT EXISTS idx_datasets_name
                    ON datasets(name);
                CREATE INDEX IF NOT EXISTS idx_dataset_items_dataset
                    ON dataset_items(dataset_id);
            """)
            await db.commit()

    # ── Dataset 操作 ──

    async def save_dataset(self, dataset: EvalDataset) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO datasets
                   (id, name, version, data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    dataset.id,
                    dataset.name,
                    dataset.version,
                    json.dumps(_strip_items(dataset).model_dump(), default=str),
                    dataset.created_at,
                    dataset.updated_at,
                ),
            )
            for item in dataset.items:
                await db.execute(
                    """INSERT OR REPLACE INTO dataset_items
                       (dataset_id, item_id, data, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        dataset.id,
                        item.id,
                        json.dumps(item.model_dump(), default=str),
                        item.created_at,
                    ),
                )
            await db.commit()

    async def get_dataset(self, dataset_id: str) -> EvalDataset | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM datasets WHERE id = ?", (dataset_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            data = json.loads(row["data"])
            cursor = await db.execute(
                "SELECT data FROM dataset_items WHERE dataset_id = ? "
                "ORDER BY created_at ASC, item_id ASC",
                (dataset_id,),
            )
            item_rows = await cursor.fetchall()
        data["items"] = [json.loads(r["data"]) for r in item_rows]
        return EvalDataset(**data)

    async def get_dataset_by_name(
        self, name: str, version: str | None = None
    ) -> EvalDataset | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if version is not None:
                cursor = await db.execute(
                    "SELECT id, version FROM datasets WHERE name = ? AND version = ?",
                    (name, version),
                )
            else:
                # 同名多版本 → 一次查询, Python 侧按 semver 取最高
                cursor = await db.execute(
                    "SELECT id, version FROM datasets WHERE name = ?", (name,)
                )
            rows = await cursor.fetchall()
        if not rows:
            return None
        best = max(rows, key=lambda r: _version_key(r["version"]))
        return await self.get_dataset(best["id"])

    async def list_datasets(self, tags: list[str] | None = None) -> list[EvalDataset]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id FROM datasets ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        datasets = []
        for row in rows:
            dataset = await self.get_dataset(row["id"])
            if dataset is None:
                continue
            if tags and not set(tags) & set(dataset.tags):
                continue
            datasets.append(dataset)
        return datasets

    async def delete_dataset(self, dataset_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM datasets WHERE id = ?", (dataset_id,)
            )
            await db.execute(
                "DELETE FROM dataset_items WHERE dataset_id = ?", (dataset_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ── Item 操作 ──

    async def save_dataset_item(self, dataset_id: str, item: EvalDatasetItem) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id FROM datasets WHERE id = ?", (dataset_id,)
            )
            if await cursor.fetchone() is None:
                raise KeyError(f"Dataset '{dataset_id}' not found")
            await db.execute(
                """INSERT OR REPLACE INTO dataset_items
                   (dataset_id, item_id, data, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    dataset_id,
                    item.id,
                    json.dumps(item.model_dump(), default=str),
                    item.created_at,
                ),
            )
            await db.commit()

    async def get_dataset_items(self, dataset_id: str) -> list[EvalDatasetItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM dataset_items WHERE dataset_id = ? "
                "ORDER BY created_at ASC, item_id ASC",
                (dataset_id,),
            )
            rows = await cursor.fetchall()
        return [EvalDatasetItem(**json.loads(r["data"])) for r in rows]

    async def get_dataset_item(
        self, dataset_id: str, item_id: str
    ) -> EvalDatasetItem | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM dataset_items WHERE dataset_id = ? AND item_id = ?",
                (dataset_id, item_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return EvalDatasetItem(**json.loads(row["data"]))

    async def delete_dataset_item(self, dataset_id: str, item_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM dataset_items WHERE dataset_id = ? AND item_id = ?",
                (dataset_id, item_id),
            )
            await db.commit()
            return cursor.rowcount > 0

