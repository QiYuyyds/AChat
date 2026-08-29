"""Unit tests for eval_harness dataset storage (Memory + SQLite)."""

import pytest
from eval_harness.core.types import GraderConfig, GraderType
from eval_harness.dataset.models import EvalDataset, EvalDatasetItem, SourceType
from eval_harness.dataset.storage import MemoryDatasetStorage, SqliteDatasetStorage


def make_item(item_id: str = "item-1", prompt: str = "hello", **overrides) -> EvalDatasetItem:
    defaults = dict(
        id=item_id,
        prompt=prompt,
        description="d",
        graders=[
            GraderConfig(type=GraderType.MODEL, name="model_based", config={"rubric": "r"})
        ],
        metadata={"capabilities": ["qa"]},
        source_type=SourceType.MANUAL,
        source_ref="ref-1",
    )
    defaults.update(overrides)
    return EvalDatasetItem(**defaults)


def make_dataset(ds_id: str = "ds_1", name: str = "core", version: str = "1.0.0",
                 tags: list[str] | None = None, items: list[EvalDatasetItem] | None = None) -> EvalDataset:
    return EvalDataset(
        id=ds_id,
        name=name,
        version=version,
        tags=tags or [],
        items=items or [],
    )


@pytest.fixture(params=["memory", "sqlite"])
async def storage(request, tmp_path):
    if request.param == "memory":
        store = MemoryDatasetStorage()
    else:
        store = SqliteDatasetStorage(str(tmp_path / "datasets.db"))
        await store.initialize()
    return store


class TestDatasetCRUD:
    async def test_save_and_get_roundtrip(self, storage):
        dataset = make_dataset(items=[make_item("i1"), make_item("i2")])
        await storage.save_dataset(dataset)

        loaded = await storage.get_dataset("ds_1")
        assert loaded is not None
        assert loaded.name == "core"
        assert loaded.version == "1.0.0"
        assert [i.id for i in loaded.items] == ["i1", "i2"]
        assert loaded.items[0].graders[0].name == "model_based"
        assert loaded.items[0].source_type == SourceType.MANUAL
        assert loaded.items[0].metadata["capabilities"] == ["qa"]

    async def test_get_missing_returns_none(self, storage):
        assert await storage.get_dataset("nope") is None

    async def test_list_datasets(self, storage):
        await storage.save_dataset(make_dataset("ds_1", name="a"))
        await storage.save_dataset(make_dataset("ds_2", name="b"))
        datasets = await storage.list_datasets()
        assert {d.id for d in datasets} == {"ds_1", "ds_2"}

    async def test_list_datasets_filter_by_tags(self, storage):
        await storage.save_dataset(make_dataset("ds_1", tags=["regression"]))
        await storage.save_dataset(make_dataset("ds_2", tags=["capability"]))
        await storage.save_dataset(make_dataset("ds_3", tags=["regression", "smoke"]))

        tagged = await storage.list_datasets(tags=["regression"])
        assert {d.id for d in tagged} == {"ds_1", "ds_3"}

        tagged = await storage.list_datasets(tags=["capability", "smoke"])
        assert {d.id for d in tagged} == {"ds_2", "ds_3"}

        assert len(await storage.list_datasets(tags=["nomatch"])) == 0
        assert len(await storage.list_datasets()) == 3

    async def test_delete_dataset_cascades_items(self, storage):
        await storage.save_dataset(make_dataset(items=[make_item("i1")]))
        assert await storage.delete_dataset("ds_1") is True
        assert await storage.get_dataset("ds_1") is None
        assert await storage.get_dataset_items("ds_1") == []
        assert await storage.delete_dataset("ds_1") is False

    async def test_save_dataset_replaces(self, storage):
        await storage.save_dataset(make_dataset(version="1.0.0"))
        await storage.save_dataset(make_dataset(version="2.0.0"))
        loaded = await storage.get_dataset("ds_1")
        assert loaded.version == "2.0.0"
        assert len(await storage.list_datasets()) == 1


class TestGetByNameAndVersion:
    async def test_exact_name_and_version(self, storage):
        await storage.save_dataset(make_dataset("ds_1", version="1.0.0"))
        await storage.save_dataset(make_dataset("ds_2", version="1.1.0"))

        found = await storage.get_dataset_by_name("core", version="1.0.0")
        assert found is not None and found.id == "ds_1"

        found = await storage.get_dataset_by_name("core", version="1.1.0")
        assert found is not None and found.id == "ds_2"

        assert await storage.get_dataset_by_name("core", version="9.9.9") is None
        assert await storage.get_dataset_by_name("missing") is None

    async def test_by_name_without_version_returns_highest(self, storage):
        await storage.save_dataset(make_dataset("ds_1", version="1.0.0"))
        await storage.save_dataset(make_dataset("ds_2", version="1.2.0"))
        await storage.save_dataset(make_dataset("ds_3", version="1.10.0"))

        found = await storage.get_dataset_by_name("core")
        assert found is not None
        assert found.id == "ds_3"  # 1.10.0 > 1.2.0 > 1.0.0 (numeric, not lexical)


class TestItemOperations:
    async def test_save_item_to_existing_dataset(self, storage):
        await storage.save_dataset(make_dataset())
        item = make_item("i9", prompt="new prompt")
        await storage.save_dataset_item("ds_1", item)

        loaded = await storage.get_dataset("ds_1")
        assert [i.id for i in loaded.items] == ["i9"]

        single = await storage.get_dataset_item("ds_1", "i9")
        assert single is not None
        assert single.prompt == "new prompt"

    async def test_save_item_to_missing_dataset_raises(self, storage):
        with pytest.raises(KeyError):
            await storage.save_dataset_item("ghost", make_item("i1"))

    async def test_update_item(self, storage):
        await storage.save_dataset(make_dataset(items=[make_item("i1")]))
        await storage.save_dataset_item("ds_1", make_item("i1", prompt="updated"))
        single = await storage.get_dataset_item("ds_1", "i1")
        assert single.prompt == "updated"
        loaded = await storage.get_dataset("ds_1")
        assert len(loaded.items) == 1

    async def test_get_item_missing_returns_none(self, storage):
        await storage.save_dataset(make_dataset())
        assert await storage.get_dataset_item("ds_1", "nope") is None

    async def test_delete_item(self, storage):
        await storage.save_dataset(make_dataset(items=[make_item("i1"), make_item("i2")]))
        assert await storage.delete_dataset_item("ds_1", "i1") is True
        assert await storage.delete_dataset_item("ds_1", "i1") is False
        loaded = await storage.get_dataset("ds_1")
        assert [i.id for i in loaded.items] == ["i2"]


class TestCompositionOnHostStorage:
    """DatasetStorage 挂到既有 Storage 实例组合暴露 (D3)."""

    async def test_memory_storage_exposes_datasets(self, tmp_path):
        from eval_harness.storage import MemoryStorage

        storage = MemoryStorage()
        assert hasattr(storage, "datasets")
        await storage.datasets.save_dataset(make_dataset())
        assert (await storage.datasets.get_dataset("ds_1")).name == "core"

    async def test_sqlite_storage_exposes_datasets_same_db(self, tmp_path):
        from eval_harness.storage import SqliteStorage

        db_path = str(tmp_path / "aeval.db")
        storage = SqliteStorage(db_path=db_path)
        await storage.initialize()

        suite_and_run_ok = storage.db_path == storage.datasets.db_path
        assert suite_and_run_ok
        await storage.datasets.save_dataset(make_dataset(items=[make_item("i1")]))

        # 独立重新打开同一文件可读到 (同一物理库)
        fresh = SqliteDatasetStorage(db_path)
        loaded = await fresh.get_dataset("ds_1")
        assert loaded is not None and [i.id for i in loaded.items] == ["i1"]
