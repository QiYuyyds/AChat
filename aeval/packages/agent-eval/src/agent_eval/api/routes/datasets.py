"""
Dataset management routes.

GET    /datasets                       — List datasets (optional ?tags=a,b)
POST   /datasets                       — Create a dataset (JSON body)
POST   /datasets/import                — Import a dataset from YAML/JSON content
GET    /datasets/{ref}                 — Get dataset with items (id or name)
DELETE /datasets/{ref}                 — Delete dataset (cascades items)
GET    /datasets/{ref}/items           — List items
POST   /datasets/{ref}/items           — Add an item
PUT    /datasets/{ref}/items/{item_id} — Update an item
DELETE /datasets/{ref}/items/{item_id} — Delete an item
POST   /datasets/{ref}/from-trace      — Mine traces into the dataset
POST   /datasets/{ref}/from-llm        — LLM-generate items into the dataset
POST   /datasets/{ref}/regression-extract — Extract failed-trial samples from a run
GET    /datasets/{ref}/quality-check   — Quality report
GET    /datasets/{ref}/coverage        — Capability coverage report
POST   /datasets/{ref}/to-suite        — Convert to an executable EvalSuite
POST   /datasets/{ref}/version         — Bump semver version with a change note
"""

from __future__ import annotations

import time
from typing import Any, Literal

from agent_eval.dataset.models import (
    DatasetError,
    EvalDataset,
    EvalDatasetItem,
    SourceType,
)
from agent_eval.dataset.quality import CoverageAnalyzer, DatasetQualityChecker
from agent_eval.dataset.sources.llm_generator import LLMDatasetGenerator
from agent_eval.dataset.sources.manual import import_from_content
from agent_eval.dataset.sources.regression import RegressionExtractor
from agent_eval.dataset.sources.trace_mining import TraceMiner
from agent_eval.dataset.version import DatasetVersionManager
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()


# ─── Request / response models ───────────────────────────────────────────────


class ImportRequest(BaseModel):
    """YAML/JSON 内容导入请求"""

    content: str = Field(..., description="数据集定义 (YAML 或 JSON 文本)")
    format: str = Field("yaml", description="内容格式: yaml | json")
    source_type: str = Field("manual", description="条目默认来源: manual | adversarial")


class FromTraceRequest(BaseModel):
    """Trace 挖掘请求"""

    strategy: str = Field("failed_tasks", description="failed_tasks | long_running | diverse_sampling")
    filters: dict[str, Any] = Field(default_factory=dict, description="get_trace_ids 过滤条件")
    limit: int = Field(20, ge=1, le=200, description="最多产出的条目数")
    candidate_limit: int = Field(100, ge=1, le=1000, description="最多检查的候选 trace 数")


class FromLLMRequest(BaseModel):
    """LLM 生成请求"""

    scenario: str = Field(..., min_length=1, description="场景描述")
    capabilities: list[str] = Field(default_factory=list, description="能力维度标签")
    count: int = Field(5, ge=1, le=50, description="请求生成的条目数")


class RegressionExtractRequest(BaseModel):
    """回归样本提取请求"""

    run_id: str = Field(..., min_length=1, description="来源 run ID")
    max_items: int = Field(50, ge=1, le=500, description="提取上限")
    bump_version: Literal["major", "minor", "patch"] | None = Field(
        None, description="合入非空时对数据集升版 (闭环惯例: minor)"
    )


class ToSuiteRequest(BaseModel):
    """to-suite 转换请求"""

    name: str | None = Field(None, description="生成的 suite 名称 (缺省用数据集名)")
    save: bool = Field(True, description="是否保存到 suite 存储 (供 POST /runs 使用)")


class VersionBumpRequest(BaseModel):
    """版本升版请求"""

    change_type: Literal["major", "minor", "patch"]
    note: str = Field("", description="变更说明 (记入 change_log)")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _require_runner():
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")
    return runner


def _require_dataset_storage(runner):
    datasets = getattr(runner.storage, "datasets", None)
    if datasets is None:
        raise HTTPException(
            status_code=503,
            detail="Dataset storage not available on this storage backend",
        )
    return datasets


async def _resolve_dataset_ref(runner, ref: str) -> EvalDataset:
    """按 ID 或名称定位数据集 (名称取最高版本)"""
    datasets = _require_dataset_storage(runner)
    dataset = await datasets.get_dataset(ref)
    if dataset is None:
        dataset = await datasets.get_dataset_by_name(ref)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{ref}' not found")
    return dataset


async def _save_dataset(runner, dataset: EvalDataset) -> None:
    datasets = _require_dataset_storage(runner)
    await datasets.save_dataset(dataset)


def _dataset_summary(dataset: EvalDataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "version": dataset.version,
        "tags": dataset.tags,
        "capability_map": dataset.capability_map,
        "item_count": len(dataset.items),
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


# ─── Dataset CRUD ────────────────────────────────────────────────────────────


@router.get("")
async def list_datasets(tags: str | None = Query(None, description="逗号分隔标签过滤")):
    """列出数据集"""
    runner = _require_runner()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    datasets = await _require_dataset_storage(runner).list_datasets(tags=tag_list)
    return {"datasets": [_dataset_summary(d) for d in datasets]}


@router.post("")
async def create_dataset(dataset: EvalDataset):
    """创建数据集 (JSON; 条目可同时携带)"""
    runner = _require_runner()
    await _save_dataset(runner, dataset)
    return {"id": dataset.id, "name": dataset.name, "version": dataset.version,
            "item_count": len(dataset.items)}


@router.post("/import")
async def import_dataset(request: ImportRequest):
    """从 YAML/JSON 内容导入数据集 (校验失败 422 并给出具体条目与字段)"""
    runner = _require_runner()
    try:
        dataset = import_from_content(
            request.content, format=request.format,
            source_type=SourceType(request.source_type) if request.source_type else SourceType.MANUAL,
            source_ref="api-import",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source_type '{request.source_type}' (valid: manual, adversarial)",
        ) from e
    except DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await _save_dataset(runner, dataset)
    return {"id": dataset.id, "name": dataset.name, "item_count": len(dataset.items)}


@router.get("/{ref}")
async def get_dataset(ref: str):
    """获取数据集详情 (含条目)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    return dataset.model_dump()


@router.delete("/{ref}")
async def delete_dataset(ref: str):
    """删除数据集 (级联删除条目)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    deleted = await _require_dataset_storage(runner).delete_dataset(dataset.id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Dataset '{ref}' not found")
    return {"deleted": True, "id": dataset.id}


# ─── Item CRUD ───────────────────────────────────────────────────────────────


@router.get("/{ref}/items")
async def list_items(ref: str):
    """列出数据集条目"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    items = await _require_dataset_storage(runner).get_dataset_items(dataset.id)
    return {"items": [i.model_dump() for i in items]}


@router.post("/{ref}/items")
async def add_item(ref: str, item: EvalDatasetItem):
    """新增条目 (ID 冲突 409)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    if dataset.get_item(item.id) is not None:
        raise HTTPException(status_code=409, detail=f"Item '{item.id}' already exists")
    try:
        await _require_dataset_storage(runner).save_dataset_item(dataset.id, item)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"added": True, "item_id": item.id, "dataset_id": dataset.id}


@router.put("/{ref}/items/{item_id}")
async def update_item(ref: str, item_id: str, item: EvalDatasetItem):
    """更新条目 (路径 ID 与 body ID 不一致时 422)"""
    if item.id != item_id:
        raise HTTPException(
            status_code=422, detail=f"Body item id '{item.id}' != path item_id '{item_id}'"
        )
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    datasets = _require_dataset_storage(runner)
    existing = await datasets.get_dataset_item(dataset.id, item_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
    try:
        await datasets.save_dataset_item(dataset.id, item)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"updated": True, "item_id": item_id}


@router.delete("/{ref}/items/{item_id}")
async def delete_item(ref: str, item_id: str):
    """删除条目"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    deleted = await _require_dataset_storage(runner).delete_dataset_item(dataset.id, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
    return {"deleted": True, "item_id": item_id}


# ─── 数据源接入 ───────────────────────────────────────────────────────────────


@router.post("/{ref}/from-trace")
async def from_trace(ref: str, request: FromTraceRequest):
    """按策略从 trace 挖掘条目并入数据集"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    miner = TraceMiner(runner.trace_provider)
    try:
        report = await miner.mine(
            request.strategy,
            filters=request.filters or None,
            limit=request.limit,
            candidate_limit=request.candidate_limit,
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        # Phoenix SDK 缺失/不可达 — 明确报错而非静默空结果
        raise HTTPException(status_code=503, detail=f"Trace provider unavailable: {e}") from e

    # 挖掘条目合入: 跳过 prompt 与既有条目重复的 trace
    extractor = RegressionExtractor()
    dataset, merge_report = extractor.merge_into_dataset(dataset, report.items)
    if report.items:
        await _save_dataset(runner, dataset)
    return {
        "mining": report.to_dict(),
        "merged": merge_report.merged,
        "merged_skipped": merge_report.merged_skipped,
        "dataset_id": dataset.id,
        "item_count": len(dataset.items),
    }


@router.post("/{ref}/from-llm")
async def from_llm(ref: str, request: FromLLMRequest):
    """LLM 按场景生成条目并入数据集 (产出经与手动导入相同的校验)"""
    runner = _require_runner()
    if runner.llm_fn is None:
        raise HTTPException(
            status_code=503,
            detail="LLM function not configured (llm_fn) — cannot generate items",
        )
    dataset = await _resolve_dataset_ref(runner, ref)
    generator = LLMDatasetGenerator(llm_fn=runner.llm_fn)
    try:
        report = await generator.generate(
            request.scenario, request.capabilities, request.count
        )
    except DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # ID 与既有条目冲突的生成条目跳过 (报告到 invalid)
    existing_ids = {i.id for i in dataset.items}
    fresh = []
    for item in report.items:
        if item.id in existing_ids:
            report.invalid.append({"index": -1, "error": f"duplicate id: {item.id}"})
        else:
            fresh.append(item)
            existing_ids.add(item.id)

    dataset = dataset.model_copy(update={
        "items": [*dataset.items, *fresh],
        "updated_at": time.time() * 1000,
    })
    if fresh:
        await _save_dataset(runner, dataset)
    return {
        "generation": report.to_dict(),
        "dataset_id": dataset.id,
        "item_count": len(dataset.items),
    }


@router.post("/{ref}/regression-extract")
async def regression_extract(ref: str, request: RegressionExtractRequest):
    """从 run 失败 trial 提取回归样本并入数据集 (质量闭环)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)

    run = await runner.storage.get_run(request.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{request.run_id}' not found")

    suite = None
    if run.suite_name:
        suite = await runner.storage.get_suite(run.suite_name)

    items, report = RegressionExtractor(max_items=request.max_items).extract_from_run(run, suite)
    dataset, merge_report = RegressionExtractor().merge_into_dataset(dataset, items)

    version_note = f"regression merge from run {request.run_id}"
    if request.bump_version and merge_report.merged > 0:
        dataset = DatasetVersionManager.bump(dataset, request.bump_version, version_note)

    if merge_report.merged:
        await _save_dataset(runner, dataset)

    return {
        "extraction": report.to_dict(),
        "merge": merge_report.to_dict(),
        "version": dataset.version,
        "bumped": bool(request.bump_version and merge_report.merged),
        "dataset_id": dataset.id,
        "item_count": len(dataset.items),
    }


# ─── 质量 / 覆盖度 ───────────────────────────────────────────────────────────


@router.get("/{ref}/quality-check")
async def quality_check(ref: str):
    """数据集质量检查 (errors 阻塞 to-suite, warnings 仅提示)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    return DatasetQualityChecker().check(dataset).to_dict()


@router.get("/{ref}/coverage")
async def coverage(ref: str, expected: str | None = Query(None, description="逗号分隔的期望能力维度")):
    """能力维度覆盖度分析"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    expected_list = (
        [c.strip() for c in expected.split(",") if c.strip()] if expected else None
    )
    return CoverageAnalyzer().analyze(dataset, expected_capabilities=expected_list).to_dict()


# ─── 转换与版本 ───────────────────────────────────────────────────────────────


@router.post("/{ref}/to-suite")
async def to_suite(ref: str, request: ToSuiteRequest):
    """转换为可执行 Suite (非法条目 422 并给出明确错误)"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    try:
        suite = dataset.to_suite(request.name)
    except DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if request.save:
        await runner.storage.save_suite(suite)
    return {
        "suite_name": suite.name,
        "task_count": len(suite.tasks),
        "metadata": suite.metadata,
        "saved": request.save,
        "dataset_id": dataset.id,
        "dataset_version": dataset.version,
    }


@router.post("/{ref}/version")
async def bump_version(ref: str, request: VersionBumpRequest):
    """语义化升版 (major/minor/patch) 并记录变更"""
    runner = _require_runner()
    dataset = await _resolve_dataset_ref(runner, ref)
    try:
        dataset = DatasetVersionManager.bump(dataset, request.change_type, request.note)
    except DatasetError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await _save_dataset(runner, dataset)
    return {
        "id": dataset.id,
        "version": dataset.version,
        "change_log": dataset.change_log,
        "item_count": len(dataset.items),
    }
