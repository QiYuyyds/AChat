"""
Core types and contracts for the Aeval evaluation framework.
"""

from agent_eval.core.types import (
    EvalTask,
    EvalSuite,
    GraderConfig,
    GraderResult,
    GraderType,
    RunResult,
    RunSummary,
    ScoreStrategy,
    TaskSummary,
    TrialResult,
)
from agent_eval.core.contract import (
    AgentRunner,
    EnvironmentManager,
    Grader,
    Storage,
    TraceProvider,
)
from agent_eval.core.metrics import aggregate_metrics, pass_at_k, pass_power_k

__all__ = [
    # Types
    "EvalTask",
    "EvalSuite",
    "GraderConfig",
    "GraderResult",
    "GraderType",
    "RunResult",
    "RunSummary",
    "ScoreStrategy",
    "TaskSummary",
    "TrialResult",
    # Contracts
    "AgentRunner",
    "EnvironmentManager",
    "Grader",
    "Storage",
    "TraceProvider",
    # Metrics
    "aggregate_metrics",
    "pass_at_k",
    "pass_power_k",
]
