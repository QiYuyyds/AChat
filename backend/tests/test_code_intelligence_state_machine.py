from pathlib import Path
from typing import get_args

import pytest


def test_state_machine_covers_every_lifecycle_state() -> None:
    from app.code_intelligence.metadata import CodeIntelligenceStatus
    from app.code_intelligence.state_machine import ALLOWED_TRANSITIONS

    assert set(ALLOWED_TRANSITIONS) == set(get_args(CodeIntelligenceStatus))


def test_state_machine_enforces_transitions_and_enabled_intent(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import MetadataStore
    from app.code_intelligence.state_machine import InvalidStateTransition, transition

    store = MetadataStore(tmp_path)
    preparing = transition(store, "preparing_runtime", phase="resolving runtime")
    queued = transition(store, "queued", phase="waiting for index slot")
    indexing = transition(store, "indexing", phase="scanning files")
    ready = transition(store, "ready", phase=None)

    assert preparing.enabled is True
    assert queued.enabled is True
    assert indexing.enabled is True
    assert ready.enabled is True
    assert ready.completed_at is not None

    with pytest.raises(InvalidStateTransition):
        transition(store, "indexing")

    disabled = transition(store, "disabled")
    assert disabled.enabled is False
    assert disabled.status == "disabled"


def test_restart_marks_orphaned_nonterminal_state_interrupted(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.state_machine import recover_interrupted

    store = MetadataStore(tmp_path)
    store.write(
        CodeIntelligenceMetadata(
            enabled=True,
            status="syncing",
            phase="updating graph",
            created_at=10,
            updated_at=20,
        )
    )

    recovered = recover_interrupted(store, has_active_task=False)

    assert recovered.status == "interrupted"
    assert recovered.enabled is True
    assert recovered.error == "Source intelligence work was interrupted by application restart"


def test_restart_keeps_terminal_or_active_state(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.state_machine import recover_interrupted

    store = MetadataStore(tmp_path)
    ready = CodeIntelligenceMetadata(enabled=True, status="ready", updated_at=20)
    store.write(ready)
    assert recover_interrupted(store, has_active_task=False) == ready

    syncing = CodeIntelligenceMetadata(enabled=True, status="syncing", updated_at=30)
    store.write(syncing)
    assert recover_interrupted(store, has_active_task=True) == syncing
