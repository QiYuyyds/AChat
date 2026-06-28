"""Prompt Assembler — cognitive slot assembly for enriched system prompts.

Ported from AGI-memory ``internal/promptctx/``.
Adaptation: ThreadPoolExecutor → asyncio.gather; Sources connect to AChat services.

Contains (all inlined per Task 4.1):
  - SlotKind, SlotFilter, Slot, ContextItem, FilledSlot (Task 4.2)
  - RuntimeContextSchema, 4 built-in schemas, slot_priority, budget (Task 4.3)
  - Query, ContextSource, 6 Source implementations (Task 4.4)
  - ContextAssembler.assemble() with asyncio.gather (Task 4.5)
  - RuntimeContext.render_system_prompt() + render_history() (Task 4.6)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Task 4.2: Slot types ────────────────────────────────────────────────────

SlotProfile = "profile"
SlotPlanner = "planner"
SlotTaskMem = "task_memory"
SlotToolState = "tool_state"
SlotConstraints = "constraints"
SlotRecall = "recall_memory"

SlotKind = str


@dataclass
class SlotFilter:
    categories: List[str] = field(default_factory=list)
    require_tags: List[str] = field(default_factory=list)
    min_score: float = 0.0
    top_k: int = 0
    max_age_hours: int = 0
    token_budget: int = 0


@dataclass
class Slot:
    kind: SlotKind
    required: bool = False
    filter: SlotFilter = field(default_factory=SlotFilter)
    template: str = ""


@dataclass
class ContextItem:
    text: str
    score: float = 0.0
    source: str = ""
    meta: Dict[str, str] = field(default_factory=dict)


@dataclass
class FilledSlot:
    kind: SlotKind
    items: List[ContextItem] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""


# ─── Task 4.3: Schemas ───────────────────────────────────────────────────────

DEFAULT_GLOBAL_TOKEN_BUDGET = 2400


@dataclass
class RuntimeContextSchema:
    mode: str
    slots: List[Slot] = field(default_factory=list)


CHAT_SCHEMA = RuntimeContextSchema(
    mode="chat",
    slots=[
        Slot(kind=SlotConstraints, filter=SlotFilter(token_budget=200)),
        Slot(kind=SlotProfile, filter=SlotFilter(
            categories=["identity", "preference"], token_budget=300, top_k=10,
        )),
        Slot(kind=SlotRecall, filter=SlotFilter(
            categories=["episodic", "fact", "general"], top_k=3, min_score=0.4, token_budget=400,
        )),
    ],
)

TOOL_SCHEMA = RuntimeContextSchema(
    mode="tool",
    slots=[
        Slot(kind=SlotConstraints, filter=SlotFilter(token_budget=200)),
        Slot(kind=SlotProfile, filter=SlotFilter(
            categories=["identity", "preference"], token_budget=250, top_k=8,
        )),
        Slot(kind=SlotToolState, required=True, filter=SlotFilter(token_budget=350, top_k=6)),
        Slot(kind=SlotRecall, filter=SlotFilter(
            categories=["episodic", "fact", "general"], top_k=2, min_score=0.5, token_budget=250,
        )),
    ],
)

REACT_SCHEMA = RuntimeContextSchema(
    mode="react",
    slots=[
        Slot(kind=SlotConstraints, required=True, filter=SlotFilter(token_budget=280)),
        Slot(kind=SlotPlanner, required=True, filter=SlotFilter(token_budget=300)),
        Slot(kind=SlotTaskMem, filter=SlotFilter(token_budget=350, top_k=8, max_age_hours=24)),
        Slot(kind=SlotToolState, required=True, filter=SlotFilter(token_budget=350, top_k=8)),
        Slot(kind=SlotProfile, filter=SlotFilter(
            categories=["identity", "preference"], token_budget=250, top_k=6,
        )),
        Slot(kind=SlotRecall, filter=SlotFilter(
            categories=["episodic", "fact", "general", "tool_failure"],
            top_k=2, min_score=0.5, token_budget=200,
        )),
    ],
)

RAG_SCHEMA = RuntimeContextSchema(
    mode="rag",
    slots=[
        Slot(kind=SlotConstraints, filter=SlotFilter(token_budget=200)),
        Slot(kind=SlotProfile, filter=SlotFilter(
            categories=["identity", "preference"], token_budget=300, top_k=8,
        )),
        Slot(kind=SlotRecall, filter=SlotFilter(
            categories=["episodic", "fact", "general"], top_k=3, min_score=0.4, token_budget=400,
        )),
    ],
)


def default_schemas() -> Dict[str, RuntimeContextSchema]:
    return {
        "chat": CHAT_SCHEMA,
        "tool": TOOL_SCHEMA,
        "react": REACT_SCHEMA,
        "rag": RAG_SCHEMA,
    }


def slot_priority(kind: SlotKind) -> int:
    priorities = {
        SlotConstraints: 0, SlotPlanner: 1, SlotTaskMem: 2,
        SlotToolState: 3, SlotProfile: 4, SlotRecall: 5,
    }
    return priorities.get(kind, 99)


# ─── Task 4.4: Sources ───────────────────────────────────────────────────────

@dataclass
class Query:
    text: str = ""
    embedding: List[float] = field(default_factory=list)
    task_id: str = ""
    mode: str = ""
    conversation_id: str = ""


class ContextSource(ABC):
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def supports(self, kind: SlotKind) -> bool: ...

    @abstractmethod
    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]: ...


class ProfileSource(ContextSource):
    """Fills profile slot from user preferences."""

    def __init__(self, preference_provider: Any = None):
        self._pref = preference_provider

    def id(self) -> str:
        return "profile"

    def supports(self, kind: SlotKind) -> bool:
        return kind in (SlotProfile, SlotRecall)

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        if self._pref is None:
            return []
        try:
            prefs = self._pref.get_all() if hasattr(self._pref, "get_all") else {}
            items = []
            for k, v in prefs.items():
                items.append(ContextItem(text=f"{k}: {v}", source="profile"))
            return items[:slot.filter.top_k] if slot.filter.top_k > 0 else items
        except Exception as e:
            logger.warning("ProfileSource fetch failed: %s", e)
            return []


class PlannerSource(ContextSource):
    """Fills planner slot from orchestrator state (stub for now)."""

    def id(self) -> str:
        return "planner"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotPlanner

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        # Planner state is not yet wired; return empty
        return []


class TaskMemSource(ContextSource):
    """Fills task memory slot from task observations."""

    def id(self) -> str:
        return "task_memory"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotTaskMem

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        return []


class ToolStateSource(ContextSource):
    """Fills tool state slot from tool registry / recent results."""

    def id(self) -> str:
        return "tool_state"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotToolState

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        return []


class ConstraintsSource(ContextSource):
    """Fills constraints slot (sandbox policy, hard rules)."""

    def __init__(self, constraints_text: str = ""):
        self._text = constraints_text

    def id(self) -> str:
        return "constraints"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotConstraints

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        if not self._text:
            return []
        return [ContextItem(text=self._text, source="constraints")]


class RecallSource(ContextSource):
    """Fills recall slot from long-term memory semantic search."""

    def __init__(self, memory_service: Any = None):
        self._memory = memory_service

    def id(self) -> str:
        return "recall"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotRecall

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        if self._memory is None or not q.text:
            return []
        try:
            items = await self._memory.recall(q.text, top_k=slot.filter.top_k or 3)
            return [
                ContextItem(
                    text=item.content,
                    score=item.score,
                    source="recall",
                    meta={"importance": str(item.importance)},
                )
                for item in items
            ]
        except Exception as e:
            logger.warning("RecallSource fetch failed: %s", e)
            return []


# ─── Task 4.5: Assembler ─────────────────────────────────────────────────────

class SourceRegistry:
    """Holds ContextSource registrations grouped by SlotKind."""

    def __init__(self) -> None:
        self._sources: Dict[SlotKind, List[ContextSource]] = {}

    def register(self, source: ContextSource) -> None:
        all_kinds = [SlotProfile, SlotPlanner, SlotTaskMem, SlotToolState, SlotConstraints, SlotRecall]
        for kind in all_kinds:
            if source.supports(kind):
                self._sources.setdefault(kind, []).append(source)

    def for_kind(self, kind: SlotKind) -> List[ContextSource]:
        return list(self._sources.get(kind, []))


class ContextAssembler:
    """Assembly entry: select Schema by Mode, concurrently fill slots, apply budget."""

    def __init__(
        self,
        schemas: Optional[Dict[str, RuntimeContextSchema]] = None,
        registry: Optional[SourceRegistry] = None,
        global_limit: int = DEFAULT_GLOBAL_TOKEN_BUDGET,
    ) -> None:
        self.schemas = schemas or default_schemas()
        self.registry = registry or SourceRegistry()
        self.global_limit = global_limit

    async def assemble(self, q: Query) -> RuntimeContext:
        schema = self.schemas.get(q.mode) or self.schemas.get("chat")
        if schema is None:
            return RuntimeContext(schema=RuntimeContextSchema(mode=q.mode))

        rc = RuntimeContext(
            schema=schema,
            filled=[FilledSlot(kind=s.kind) for s in schema.slots],
        )

        slots = list(schema.slots)
        if slots:
            tasks = [self._fill_slot(slot, q) for slot in slots]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning("promptctx fill_slot failed: %s", result)
                    rc.filled[idx] = FilledSlot(
                        kind=slots[idx].kind,
                        skipped=not slots[idx].required,
                        reason=f"source error: {result}",
                    )
                else:
                    rc.filled[idx] = result

        self._apply_global_budget(rc)
        return rc

    async def _fill_slot(self, slot: Slot, q: Query) -> FilledSlot:
        sources = self.registry.for_kind(slot.kind)
        if not sources:
            return FilledSlot(kind=slot.kind, skipped=not slot.required, reason="no source registered")

        all_items: List[ContextItem] = []
        for src in sources:
            try:
                items = await src.fetch(slot, q) or []
            except Exception as e:
                logger.warning("promptctx source %s fetch failed: %s", src.id(), e)
                break
            all_items.extend(items)

        if not all_items:
            return FilledSlot(kind=slot.kind, skipped=not slot.required, reason="source returned empty")

        if slot.filter.token_budget > 0:
            all_items = _trim_by_budget(all_items, slot.filter.token_budget)

        return FilledSlot(kind=slot.kind, items=all_items)

    def _apply_global_budget(self, rc: RuntimeContext) -> None:
        total = sum(len(item.text) for fs in rc.filled for item in fs.items)
        if total <= self.global_limit:
            return

        order = list(range(len(rc.filled)))
        order.sort(key=lambda i: slot_priority(rc.filled[i].kind), reverse=True)

        for idx in order:
            if total <= self.global_limit:
                break
            fs = rc.filled[idx]
            while fs.items and total > self.global_limit:
                last = fs.items[-1]
                total -= len(last.text)
                fs.items = fs.items[:-1]
            if not fs.items:
                fs.skipped = not rc.schema.slots[idx].required
                fs.reason = "global budget exceeded"


def _trim_by_budget(items: List[ContextItem], budget: int) -> List[ContextItem]:
    total = 0
    for i, item in enumerate(items):
        total += len(item.text)
        if total > budget:
            return items[:i]
    return items


# ─── Task 4.6: RuntimeContext + render ────────────────────────────────────────

@dataclass
class RuntimeContext:
    schema: RuntimeContextSchema
    filled: List[FilledSlot] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)

    def slot_by_kind(self, kind: SlotKind) -> Optional[FilledSlot]:
        for fs in self.filled:
            if fs.kind == kind:
                return fs
        return None

    def render(self) -> str:
        """Render all non-empty slots in schema order as zh-CN prompt prefix."""
        if not self.filled:
            return ""
        sections = []
        for fs in self.filled:
            if fs.skipped or not fs.items:
                continue
            rendered = _render_slot(fs)
            if rendered:
                sections.append(rendered)
        return "\n\n".join(sections)

    def render_system_prompt(self, base_prompt: str = "") -> str:
        """Render full system prompt: base + context prefix."""
        ctx = self.render()
        if not ctx:
            return base_prompt
        if base_prompt:
            return f"{base_prompt}\n\n{ctx}"
        return ctx

    def render_history(self) -> List[Dict[str, str]]:
        """Render context as OpenAI chat format messages."""
        ctx = self.render()
        if not ctx:
            return []
        return [{"role": "system", "content": ctx}]


def _render_slot(fs: FilledSlot) -> str:
    title = _slot_title(fs.kind)
    lines = []
    for item in fs.items:
        if item.text and item.text.strip():
            lines.append("- " + item.text)
    if not lines:
        return ""
    return f"【{title}】\n" + "\n".join(lines)


def _slot_title(kind: SlotKind) -> str:
    titles = {
        SlotProfile: "用户画像", SlotPlanner: "任务规划",
        SlotTaskMem: "任务记忆", SlotToolState: "可用工具",
        SlotConstraints: "硬性约束", SlotRecall: "相关回忆",
    }
    return titles.get(kind, str(kind))
