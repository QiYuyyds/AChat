"""Build and recover the optional source-intelligence service."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import select

from app.code_intelligence.index_manager import CodeIntelligenceManager
from app.code_intelligence.metadata import MetadataStore
from app.code_intelligence.process_runner import CodeGraphCommandRunner
from app.code_intelligence.runtime import RuntimeManager
from app.code_intelligence.service import CodeIntelligenceService
from app.code_intelligence.state_machine import recover_interrupted
from app.config import Settings
from app.db.engine import get_db
from app.db.models import Workspace


def build_code_intelligence_service(settings: Settings) -> CodeIntelligenceService:
    repository_root = Path(__file__).resolve().parents[3]
    packaged_root = Path(
        os.environ.get(
            "AGENTHUB_CODEGRAPH_RESOURCES",
            repository_root / "resources" / "codegraph",
        )
    ).resolve()
    cache_root = settings.data_path / "runtimes" / "codegraph"
    runtime_manager = RuntimeManager(
        packaged_root=packaged_root,
        cache_root=cache_root,
    )
    command_runner = CodeGraphCommandRunner(runtime_manager)
    index_manager = CodeIntelligenceManager(
        runner=command_runner.run_index,
        max_concurrency=1,
    )
    return CodeIntelligenceService(
        runtime_manager=runtime_manager,
        index_manager=index_manager,
        command_runner=command_runner,
    )


async def recover_code_intelligence_metadata() -> None:
    async with get_db() as db:
        result = await db.execute(select(Workspace).where(Workspace.mode == "local"))
        workspaces = result.scalars().all()
    for workspace in workspaces:
        recover_interrupted(
            MetadataStore(Path(workspace.root_path)),
            has_active_task=False,
        )
