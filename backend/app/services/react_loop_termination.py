"""Custom/SDK ReAct loop termination state machine.

Model-done (0 tool calls) is the primary success path. There is no small default
max-turns product cap. Context budget, optional max_tool_turns fuse, and
behavioral circuit breakers share one pre-model decision pipeline:

  compact (~90%) → soft wrap-up (~93%) → forced final → hard stop (~95%)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ─── Thresholds (v1: code constants) ─────────────────────────────────────────
# Five-stage compaction pipeline. Stages 1/2/3 replace the single-point compact
# when ``compact_pipeline_enabled`` is True (default). Stage 4 (soft_inject)
# and stage 5 (force_final) are unchanged.
STAGE1_RATIO = 0.70  # semantic summarization (regex-only, no LLM)
STAGE2_RATIO = 0.80  # moderate pruning (re-prune stage-1 summaries)
STAGE3_RATIO = 0.88  # turn-boundary folding (older turns → single marker)

# Legacy single-point compact threshold. Lowered from 0.90 → 0.85 to compensate
# for the content-only token estimator (excludes JSON metadata, ~15-25% lower).
# Only used when ``compact_pipeline_enabled=False`` (legacy rollback path).
COMPACT_RATIO = 0.85
SOFT_RATIO = 0.93
HARD_RATIO = 0.95

# Absolute safety bound against infinite loops / runaway (not a product default).
SAFETY_MAX_MODEL_CALLS = 10_000

# Circuit breaker thresholds
DUPLICATE_FINGERPRINT_THRESHOLD = 3
TOOL_ERROR_THRESHOLD = 3
COMPACT_FAILURE_THRESHOLD = 3

# Volatile arg keys excluded from fingerprints (prefer under-trigger).
_VOLATILE_ARG_KEYS = frozenset({
    "timestamp",
    "ts",
    "request_id",
    "requestId",
    "uuid",
    "id",
    "nonce",
    "created_at",
    "createdAt",
    "updated_at",
    "updatedAt",
    "session_id",
    "sessionId",
    "trace_id",
    "traceId",
    "time",
    "datetime",
    "date",
})

_PATH_ARG_KEYS = frozenset({
    "path",
    "file",
    "file_path",
    "filePath",
    "filepath",
    "dir",
    "directory",
    "cwd",
    "target",
    "src",
    "dst",
    "source",
    "destination",
})


class StopReason(StrEnum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BUDGET_SOFT_COMPLETE = "budget_soft_complete"
    BUDGET_FORCED_FINAL = "budget_forced_final"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DUPLICATE_TOOL_BREAKER = "duplicate_tool_breaker"
    TOOL_ERROR_BREAKER = "tool_error_breaker"
    COMPACT_FAILURE_BREAKER = "compact_failure_breaker"
    MAX_TOOL_TURNS = "max_tool_turns"


STOP_REASON_LABELS: dict[str, str] = {
    StopReason.COMPLETE.value: "",
    StopReason.CANCELLED.value: "运行已取消",
    StopReason.BUDGET_SOFT_COMPLETE.value: "因上下文接近上限，已自动收尾",
    StopReason.BUDGET_FORCED_FINAL.value: "因上下文接近上限，已自动总结",
    StopReason.BUDGET_EXHAUSTED.value: "上下文已用尽，未能完成完整收尾",
    StopReason.DUPLICATE_TOOL_BREAKER.value: "检测到重复操作，已自动总结",
    StopReason.TOOL_ERROR_BREAKER.value: "同一工具连续失败，已自动总结",
    StopReason.COMPACT_FAILURE_BREAKER.value: "上下文压缩失败，已自动总结",
    StopReason.MAX_TOOL_TURNS.value: "达到操作轮次上限，已自动收尾",
}


def stop_reason_label(reason: StopReason | str | None) -> str | None:
    """Return short Chinese UI label, or None for natural complete / unknown empty."""
    if reason is None:
        return None
    key = reason.value if isinstance(reason, StopReason) else str(reason)
    label = STOP_REASON_LABELS.get(key, "")
    return label or None


SOFT_WRAPUP_INSTRUCTION = (
    "[系统提示·请勿在回复中复述本段] 上下文预算接近上限。"
    "请尽快收束当前工作：优先给出用户可用的结论与下一步建议；"
    "仅在完成收尾所必需时再调用工具，否则请直接回复总结。"
)

BREAKER_DUPLICATE_INSTRUCTION = (
    "[系统提示·请勿在回复中复述本段] 检测到你连续多次以相同参数调用同一工具。"
    "请更换策略，或直接总结当前进展与阻塞，不要再重复同一调用。"
)

BREAKER_TOOL_ERROR_INSTRUCTION = (
    "[系统提示·请勿在回复中复述本段] 同一工具连续执行失败。"
    "请改用其他方法，或总结失败原因与建议下一步，不要继续盲目重试。"
)

FORCED_FINAL_INSTRUCTION = (
    "[系统提示·请勿在回复中复述本段] 本轮为强制收尾：不能再调用任何工具。"
    "请用面向用户的自然语言总结：\n"
    "1. 已完成的工作\n"
    "2. 未完成或部分完成的工作\n"
    "3. 阻塞 / 风险\n"
    "4. 建议的下一步\n"
    "若因预算或断路器停止，不要假装任务已 100% 完成。"
)


DecisionAction = Literal[
    "continue",
    # Five-stage pipeline (stages 1/2/3, only when pipeline enabled)
    "summarize",   # stage 1: semantic summarization of old tool results
    "prune",       # stage 2: moderate re-pruning of stage-1 summaries
    "fold",        # stage 3: fold older turns into a single marker
    # Legacy single-point compact (only when pipeline disabled)
    "compact",
    # Stage 4/5 (unchanged)
    "soft_inject",
    "force_final",
    "hard_stop",
]


@dataclass
class PreModelDecision:
    action: DecisionAction
    stop_reason: StopReason | None = None
    inject_message: str | None = None
    # When action is force_final, which reason to record if forced produces the end.
    pending_reason: StopReason | None = None


@dataclass
class TerminationState:
    """Mutable per-run termination / breaker state for the Custom ReAct loop."""

    soft_done: bool = False
    forced_done: bool = False
    # tool-use turns completed (a turn that executed ≥1 tool)
    tool_turn_count: int = 0
    # model calls issued
    model_call_count: int = 0
    # optional fuse from settings (None = off)
    max_tool_turns: int | None = None

    # budget path
    compact_consecutive_failures: int = 0
    compact_disabled: bool = False

    # breakers
    last_fingerprint: str | None = None
    consecutive_fingerprint_count: int = 0
    duplicate_inject_done: bool = False
    force_after_duplicate: bool = False

    last_error_tool: str | None = None
    consecutive_tool_errors: int = 0
    tool_error_inject_done: bool = False
    force_after_tool_error: bool = False

    # Soft wrap-up was ignored (model still emitted tools) → force next call
    force_after_soft: bool = False

    # when soft was triggered for a specific reason (for soft-complete labeling)
    soft_trigger_reason: StopReason | None = None
    # forced final pending reason
    forced_trigger_reason: StopReason | None = None

    # harness facts for forced final
    tool_names_used: list[str] = field(default_factory=list)
    tool_error_count: int = 0

    final_stop_reason: StopReason = StopReason.COMPLETE

    def record_tool_calls(self, names: list[str], fingerprints: list[str], errors: list[bool]) -> None:
        """Update breaker counters after a tool-execution turn."""
        self.tool_turn_count += 1
        for name in names:
            self.tool_names_used.append(name)

        # Duplicate fingerprint: only consecutive identical across sequential calls
        # within the turn, we consider each tool call in order for the streak.
        for fp in fingerprints:
            if fp and fp == self.last_fingerprint:
                self.consecutive_fingerprint_count += 1
            else:
                self.last_fingerprint = fp
                self.consecutive_fingerprint_count = 1 if fp else 0

            if (
                self.consecutive_fingerprint_count >= DUPLICATE_FINGERPRINT_THRESHOLD
                and self.duplicate_inject_done
            ):
                self.force_after_duplicate = True
            elif self.consecutive_fingerprint_count >= DUPLICATE_FINGERPRINT_THRESHOLD:
                # Will inject on next pre-model decision
                pass

        # Same-tool consecutive errors: streak only when consecutive errors share name
        for name, is_err in zip(names, errors, strict=False):
            if is_err:
                self.tool_error_count += 1
                if name == self.last_error_tool:
                    self.consecutive_tool_errors += 1
                else:
                    self.last_error_tool = name
                    self.consecutive_tool_errors = 1
                if (
                    self.consecutive_tool_errors >= TOOL_ERROR_THRESHOLD
                    and self.tool_error_inject_done
                ):
                    self.force_after_tool_error = True
            else:
                self.last_error_tool = None
                self.consecutive_tool_errors = 0

    def build_harness_digest(self) -> str:
        from collections import Counter

        counts = Counter(self.tool_names_used)
        top = ", ".join(f"{n}×{c}" for n, c in counts.most_common(12)) or "(无)"
        return (
            f"本 run 工具调用摘要：{top}；"
            f"工具轮次={self.tool_turn_count}；"
            f"工具失败次数≈{self.tool_error_count}；"
            f"触发原因={self.forced_trigger_reason.value if self.forced_trigger_reason else 'budget'}。"
        )


def _normalize_path_value(value: str) -> str:
    s = value.replace("\\", "/").strip()
    # Drop drive letters on Windows-style paths for stability
    if re.match(r"^[A-Za-z]:/", s):
        s = s[2:]
    try:
        s = PurePosixPath(s).as_posix()
    except Exception:
        s = PureWindowsPath(value).as_posix()
    # Collapse // and trailing /
    while "//" in s:
        s = s.replace("//", "/")
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def _normalize_args(args: Any) -> Any:
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for k in sorted(args.keys(), key=lambda x: str(x)):
            if str(k) in _VOLATILE_ARG_KEYS:
                continue
            v = args[k]
            if str(k) in _PATH_ARG_KEYS and isinstance(v, str):
                out[str(k)] = _normalize_path_value(v)
            else:
                out[str(k)] = _normalize_args(v)
        return out
    if isinstance(args, list):
        return [_normalize_args(x) for x in args]
    if (
        isinstance(args, str)
        and ("/" in args or "\\" in args)
        and len(args) < 512
        and (re.search(r"[\\/].+[\\/]", args) or args.startswith(("./", "../", "/")))
    ):
        return _normalize_path_value(args)
    return args


def stable_tool_fingerprint(tool_name: str, args: Any) -> str:
    """Stable fingerprint for circuit breakers. Prefer under-trigger on uncertainty."""
    try:
        normalized = _normalize_args(args if isinstance(args, (dict, list)) else {"_": args})
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(args)[:500]
    return f"{tool_name}|{payload}"


def decide_pre_model(
    *,
    state: TerminationState,
    total_tokens: int,
    model_limit: int,
    pipeline_enabled: bool = True,
) -> PreModelDecision:
    """Single pre-model-call decision for the Custom termination pipeline.

    When ``pipeline_enabled`` is True (default), stages 1/2/3 replace the
    single-point compact: ratio ≥ 0.70 → summarize, ≥ 0.80 → prune, ≥ 0.88 →
    fold. When False, the legacy single-point ``compact`` at ``COMPACT_RATIO``
    (0.85) is used instead. Stages 4/5 (soft_inject / force_final) are
    unchanged regardless of the toggle.
    """
    if state.forced_done:
        return PreModelDecision(
            action="hard_stop",
            stop_reason=state.final_stop_reason or StopReason.BUDGET_EXHAUSTED,
        )

    if state.model_call_count >= SAFETY_MAX_MODEL_CALLS:
        return PreModelDecision(
            action="hard_stop",
            stop_reason=StopReason.BUDGET_EXHAUSTED,
        )

    ratio = (total_tokens / model_limit) if model_limit > 0 else 0.0

    # Soft ignored or breaker re-trip → forced final
    if state.soft_done and (
        state.force_after_soft
        or state.force_after_duplicate
        or state.force_after_tool_error
    ):
        if state.force_after_duplicate:
            reason = StopReason.DUPLICATE_TOOL_BREAKER
        elif state.force_after_tool_error:
            reason = StopReason.TOOL_ERROR_BREAKER
        else:
            reason = state.soft_trigger_reason or StopReason.BUDGET_FORCED_FINAL
            if reason == StopReason.BUDGET_SOFT_COMPLETE:
                reason = StopReason.BUDGET_FORCED_FINAL
        state.forced_trigger_reason = reason
        return PreModelDecision(
            action="force_final",
            pending_reason=reason,
        )

    # max_tool_turns fuse → soft → forced
    fuse_hit = (
        state.max_tool_turns is not None
        and state.max_tool_turns > 0
        and state.tool_turn_count >= state.max_tool_turns
    )

    # Compact failure breaker → soft/forced pipeline
    compact_breaker = state.compact_disabled

    # Duplicate inject (before soft_done)
    if (
        state.consecutive_fingerprint_count >= DUPLICATE_FINGERPRINT_THRESHOLD
        and not state.duplicate_inject_done
        and not state.soft_done
    ):
        state.duplicate_inject_done = True
        state.soft_done = True
        state.soft_trigger_reason = StopReason.DUPLICATE_TOOL_BREAKER
        return PreModelDecision(
            action="soft_inject",
            inject_message=BREAKER_DUPLICATE_INSTRUCTION,
            pending_reason=StopReason.DUPLICATE_TOOL_BREAKER,
        )

    if (
        state.consecutive_tool_errors >= TOOL_ERROR_THRESHOLD
        and not state.tool_error_inject_done
        and not state.soft_done
    ):
        state.tool_error_inject_done = True
        state.soft_done = True
        state.soft_trigger_reason = StopReason.TOOL_ERROR_BREAKER
        return PreModelDecision(
            action="soft_inject",
            inject_message=BREAKER_TOOL_ERROR_INSTRUCTION,
            pending_reason=StopReason.TOOL_ERROR_BREAKER,
        )

    # After soft, if still requesting tools due to breaker / fuse / budget → force
    needs_wrap = fuse_hit or compact_breaker or ratio >= SOFT_RATIO or ratio >= HARD_RATIO

    if needs_wrap:
        if not state.soft_done:
            reason = (
                StopReason.MAX_TOOL_TURNS if fuse_hit
                else StopReason.COMPACT_FAILURE_BREAKER if compact_breaker
                else StopReason.BUDGET_SOFT_COMPLETE
            )
            state.soft_done = True
            state.soft_trigger_reason = reason
            inject = SOFT_WRAPUP_INSTRUCTION
            if fuse_hit:
                inject = (
                    "[系统提示·请勿在回复中复述本段] 已达到操作轮次上限。"
                    "请尽快收束并给出用户可用的总结与下一步建议；"
                    "仅在必要时调用工具，否则直接回复。"
                )
            elif compact_breaker:
                inject = (
                    "[系统提示·请勿在回复中复述本段] 上下文压缩多次失败，预算紧张。"
                    "请尽快总结并停止扩展上下文的操作。"
                )
            return PreModelDecision(
                action="soft_inject",
                inject_message=inject,
                pending_reason=reason,
            )

        # soft already done → force (guarantee wrap-up even past hard threshold)
        if not state.forced_done:
            reason = (
                StopReason.MAX_TOOL_TURNS if fuse_hit and state.soft_trigger_reason == StopReason.MAX_TOOL_TURNS
                else state.soft_trigger_reason
                or (StopReason.COMPACT_FAILURE_BREAKER if compact_breaker else StopReason.BUDGET_FORCED_FINAL)
            )
            # Map soft-complete reason to forced when forcing due to continued tools
            if reason == StopReason.BUDGET_SOFT_COMPLETE:
                reason = StopReason.BUDGET_FORCED_FINAL
            state.forced_trigger_reason = reason
            return PreModelDecision(
                action="force_final",
                pending_reason=reason,
            )

        return PreModelDecision(
            action="hard_stop",
            stop_reason=StopReason.BUDGET_EXHAUSTED,
        )

    # Compact band: only when below soft and compact still allowed.
    # When pipeline_enabled, use five-stage pipeline (stages 1/2/3).
    # When not, use legacy single-point compact at COMPACT_RATIO.
    if model_limit > 0 and not state.compact_disabled:
        if pipeline_enabled:
            if ratio >= STAGE3_RATIO:
                return PreModelDecision(action="fold")
            if ratio >= STAGE2_RATIO:
                return PreModelDecision(action="prune")
            if ratio >= STAGE1_RATIO:
                return PreModelDecision(action="summarize")
        elif ratio >= COMPACT_RATIO:
            return PreModelDecision(action="compact")

    # Soft already done but model is continuing with tools below soft threshold
    # (e.g. soft was from breaker inject, model keeps going with tools)
    if state.soft_done and (
        state.force_after_duplicate
        or state.force_after_tool_error
        or (
            state.soft_trigger_reason
            in (
                StopReason.DUPLICATE_TOOL_BREAKER,
                StopReason.TOOL_ERROR_BREAKER,
                StopReason.MAX_TOOL_TURNS,
                StopReason.COMPACT_FAILURE_BREAKER,
            )
            # force only if they keep calling tools — handled after tool turn when
            # we re-enter with force flags. Here: if soft done from budget and
            # still under soft threshold, allow continue until next soft/hard hit.
        )
    ):
        # force flags handled above when soft_done; if soft done from breaker but
        # force flag not yet set, allow one more free turn until breaker re-trips.
        pass

    return PreModelDecision(action="continue")


def mark_compact_result(state: TerminationState, *, success: bool) -> None:
    if success:
        state.compact_consecutive_failures = 0
        return
    state.compact_consecutive_failures += 1
    if state.compact_consecutive_failures >= COMPACT_FAILURE_THRESHOLD:
        state.compact_disabled = True
        logger.warning(
            "[react-termination] compact failed %d times; disabling further compact",
            state.compact_consecutive_failures,
        )


def build_forced_messages(state: TerminationState) -> list[dict]:
    """System messages to inject for a forced final call."""
    digest = state.build_harness_digest()
    return [
        {"role": "system", "content": FORCED_FINAL_INSTRUCTION},
        {"role": "system", "content": f"[harness 事实摘要] {digest}"},
    ]


def format_child_stop_prefix(stop_reason: StopReason | str | None, final_text: str) -> str:
    """Annotate child run tool result for non-complete stops."""
    if stop_reason is None:
        return final_text
    key = stop_reason.value if isinstance(stop_reason, StopReason) else str(stop_reason)
    if key in ("", StopReason.COMPLETE.value):
        return final_text
    return f"[stopped: {key}]\n{final_text}"
