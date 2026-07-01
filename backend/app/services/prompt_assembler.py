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
import time
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


# ─── Task 2-4: Buffer / Tracker / Snapshot data classes ──────────────────────


@dataclass
class StepObservation:
    """A single tool-execution observation pushed to TaskMemBuffer."""

    step_id: str = ""
    tool_name: str = ""
    result: str = ""
    error: str = ""
    success: bool = True
    created_at: float = field(default_factory=time.time)


class TaskMemBuffer:
    """Async ring buffer for step observations (max_size, LIFO-friendly).

    Uses ``asyncio.Lock`` to stay safe in async contexts. When the buffer
    exceeds *max_size* the oldest entry is discarded.
    """

    def __init__(self, max_size: int = 20) -> None:
        self._max_size = max_size
        self._buf: List[StepObservation] = []
        self._lock = asyncio.Lock()

    async def push(self, obs: StepObservation) -> None:
        async with self._lock:
            self._buf.append(obs)
            if len(self._buf) > self._max_size:
                self._buf = self._buf[-self._max_size :]

    async def reset(self) -> None:
        async with self._lock:
            self._buf.clear()

    async def snapshot(self) -> List[StepObservation]:
        async with self._lock:
            return list(self._buf)


@dataclass
class ToolCallTrace:
    """A single tool-call trace recorded in ToolStateTracker."""

    tool_name: str = ""
    success: bool = True
    summary: str = ""
    created_at: float = field(default_factory=time.time)


class ToolStateTracker:
    """Async ring buffer for tool-call traces.

    Summaries are truncated to 120 characters on record. Max size defaults
    to 10; the oldest entry is discarded when exceeded.
    """

    _SUMMARY_LIMIT = 120

    def __init__(self, max_size: int = 10) -> None:
        self._max_size = max_size
        self._buf: List[ToolCallTrace] = []
        self._lock = asyncio.Lock()

    async def record(self, trace: ToolCallTrace) -> None:
        async with self._lock:
            if len(trace.summary) > self._SUMMARY_LIMIT:
                trace.summary = trace.summary[: self._SUMMARY_LIMIT] + "…"
            self._buf.append(trace)
            if len(self._buf) > self._max_size:
                self._buf = self._buf[-self._max_size :]

    async def snapshot(self) -> List[ToolCallTrace]:
        async with self._lock:
            return list(self._buf)


@dataclass
class PlannerSnapshot:
    """Snapshot of the current dispatch plan state for prompt injection."""

    task_id: str = ""
    query: str = ""
    status: str = ""          # running | completed | interrupted | idle
    phase: str = ""           # planning | executing | aggregating
    total_steps: int = 0
    current_step: int = 0
    interrupted_at: str = ""
    next_step_name: str = ""
    next_step_tool: str = ""


# Provider callbacks — return None when no data is available (safe degrade).
PlannerProvider = Callable[[], Optional[PlannerSnapshot]]
ToolRegistryProvider = Callable[[], Dict[str, Any]]


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
    """Fills profile slot from user preferences AND LTM identity/preference items."""

    def __init__(self, preference_provider: Any = None, ltm: Any = None):
        self._pref = preference_provider
        self._ltm = ltm  # Optional[LongTerm] with filter_by_category()

    def id(self) -> str:
        return "profile"

    def supports(self, kind: SlotKind) -> bool:
        return kind in (SlotProfile, SlotRecall)

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        items: List[ContextItem] = []
        top_k = slot.filter.top_k or 0

        # 1. Preference key-value pairs (score=1.0)
        if self._pref is not None:
            try:
                prefs = self._pref.get_all() if hasattr(self._pref, "get_all") else {}
                for k, v in prefs.items():
                    items.append(
                        ContextItem(text=f"{k}: {v}", score=1.0, source="profile")
                    )
            except Exception as e:
                logger.warning("ProfileSource preference fetch failed: %s", e)

        # 2. LTM items filtered by category (score=importance)
        if self._ltm is not None:
            try:
                categories = slot.filter.categories or ["identity", "preference"]
                ltm_limit = top_k if top_k > 0 else 10
                ltm_items = await self._ltm.filter_by_category(categories, ltm_limit)
                for it in ltm_items:
                    items.append(
                        ContextItem(
                            text=it.content,
                            score=it.importance,
                            source="profile",
                            meta={"category": it.category or "general"},
                        )
                    )
            except Exception as e:
                logger.warning("ProfileSource LTM fetch failed: %s", e)

        if top_k > 0 and len(items) > top_k:
            items = items[:top_k]
        return items


class PlannerSource(ContextSource):
    """Fills planner slot from dispatch plan state via PlannerProvider."""

    def __init__(self, provider: Optional[PlannerProvider] = None):
        self._provider = provider

    def id(self) -> str:
        return "planner"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotPlanner

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        if self._provider is None:
            return []
        try:
            snap = self._provider()
        except Exception as e:
            logger.warning("PlannerSource provider error: %s", e)
            return []
        if snap is None:
            return []

        items: List[ContextItem] = []
        # Task status line
        items.append(
            ContextItem(
                text=f"任务 {snap.task_id} 状态={snap.status} 阶段={snap.phase}",
                source="planner",
            )
        )
        # Progress line
        if snap.total_steps > 0:
            items.append(
                ContextItem(
                    text=f"进度：第 {snap.current_step + 1}/{snap.total_steps} 步",
                    source="planner",
                )
            )
        # Next step hint
        if snap.next_step_name:
            tool_info = f" [{snap.next_step_tool}]" if snap.next_step_tool else ""
            items.append(
                ContextItem(
                    text=f"下一步：{snap.next_step_name}{tool_info}",
                    source="planner",
                )
            )
        # Interruption recovery
        if snap.interrupted_at:
            items.append(
                ContextItem(
                    text=f"中断恢复：{snap.interrupted_at}",
                    source="planner",
                )
            )
        return items


class TaskMemSource(ContextSource):
    """Fills task memory slot from TaskMemBuffer step observations."""

    def __init__(self, buffer: Optional[TaskMemBuffer] = None):
        self._buffer = buffer

    def id(self) -> str:
        return "task_memory"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotTaskMem

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        if self._buffer is None:
            return []
        try:
            observations = await self._buffer.snapshot()
        except Exception as e:
            logger.warning("TaskMemSource snapshot error: %s", e)
            return []
        if not observations:
            return []

        top_k = slot.filter.top_k or 0
        if top_k > 0 and len(observations) > top_k:
            observations = observations[-top_k:]  # keep most recent

        items: List[ContextItem] = []
        for obs in observations:
            if obs.success:
                text = f"步骤{obs.step_id} [{obs.tool_name}]→{obs.result}"
            else:
                text = f"步骤{obs.step_id} [{obs.tool_name}] 失败: {obs.error}"
            items.append(ContextItem(text=text, source="task_memory"))
        return items


class ToolStateSource(ContextSource):
    """Fills tool state slot from tool registry + recent call traces."""

    def __init__(
        self,
        registry_provider: Optional[ToolRegistryProvider] = None,
        tracker: Optional[ToolStateTracker] = None,
    ):
        self._registry_provider = registry_provider
        self._tracker = tracker

    def id(self) -> str:
        return "tool_state"

    def supports(self, kind: SlotKind) -> bool:
        return kind == SlotToolState

    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        items: List[ContextItem] = []
        top_k = slot.filter.top_k or 0

        # 1. Available tool list
        if self._registry_provider is not None:
            try:
                tools = self._registry_provider() or {}
                for name, tool in tools.items():
                    desc = ""
                    if hasattr(tool, "description"):
                        desc = tool.description
                    elif isinstance(tool, dict):
                        desc = tool.get("description", "")
                    items.append(
                        ContextItem(
                            text=f"{name} — {desc}",
                            source="tool_state",
                        )
                    )
            except Exception as e:
                logger.warning("ToolStateSource registry error: %s", e)

        # 2. Recent call traces
        if self._tracker is not None:
            try:
                traces = await self._tracker.snapshot()
            except Exception as e:
                logger.warning("ToolStateSource tracker error: %s", e)
                traces = []
            for t in traces:
                status = "成功" if t.success else "失败"
                items.append(
                    ContextItem(
                        text=f"近期调用 {t.tool_name} [{status}]: {t.summary}",
                        source="tool_state",
                    )
                )

        if top_k > 0 and len(items) > top_k:
            items = items[:top_k]
        return items


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
