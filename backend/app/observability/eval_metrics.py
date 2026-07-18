"""Evaluation metric definitions — Agent 全过程 + 多 Agent 协作.

Each metric has a name, dimension, evaluation method (rule / judge / compare),
and a human-readable description.  Used by both online rule evaluation and
offline LLM-as-judge evaluation to label scores consistently.
"""

from dataclasses import dataclass
from enum import Enum


class EvalMethod(str, Enum):
    RULE = "rule"
    JUDGE = "judge"
    COMPARE = "compare"


class AgentDimension(str, Enum):
    TASK_COMPLETION = "task_completion"
    TOOL_QUALITY = "tool_quality"
    STEP_EFFICIENCY = "step_efficiency"
    PROMPT_EFFECT = "prompt_effect"
    ANSWER_QUALITY = "answer_quality"


class CollaborationDimension(str, Enum):
    TASK_DECOMPOSITION = "task_decomposition"
    DISPATCH_EFFICIENCY = "dispatch_efficiency"
    SUBAGENT_QUALITY = "subagent_quality"
    AGGREGATION_QUALITY = "aggregation_quality"


@dataclass(frozen=True)
class MetricDef:
    name: str
    dimension: str
    method: EvalMethod
    description: str


# ── Agent 全过程 (5 dimensions) ──

AGENT_METRICS: list[MetricDef] = [
    # Task Completion
    MetricDef("task_completion_rate", AgentDimension.TASK_COMPLETION.value, EvalMethod.RULE,
              "1.0 if finish_reason=='end_turn' else 0.0"),
    MetricDef("output_artifact_exists", AgentDimension.TASK_COMPLETION.value, EvalMethod.RULE,
              "1.0 if an artifact was produced"),
    MetricDef("max_turns_exceeded", AgentDimension.TASK_COMPLETION.value, EvalMethod.RULE,
              "0.0 if abnormal Custom stop_reason or safety-bound turns; 1.0 for model-done"),
    # Tool Quality
    MetricDef("tool_success_rate", AgentDimension.TOOL_QUALITY.value, EvalMethod.RULE,
              "successful tool calls / total tool calls"),
    MetricDef("tool_selection_accuracy", AgentDimension.TOOL_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges if model selected the right tools"),
    MetricDef("tool_call_efficiency", AgentDimension.TOOL_QUALITY.value, EvalMethod.RULE,
              "1.0 - redundant_tool_calls_ratio"),
    MetricDef("redundant_tool_calls", AgentDimension.TOOL_QUALITY.value, EvalMethod.RULE,
              "duplicate (tool+args) calls / total calls"),
    # Step Efficiency
    MetricDef("turns_to_complete", AgentDimension.STEP_EFFICIENCY.value, EvalMethod.RULE,
              "total turns taken"),
    MetricDef("token_usage", AgentDimension.STEP_EFFICIENCY.value, EvalMethod.RULE,
              "sum(input_tokens) + sum(output_tokens)"),
    MetricDef("latency_per_turn", AgentDimension.STEP_EFFICIENCY.value, EvalMethod.RULE,
              "duration_ms / total_turns"),
    MetricDef("tool_vs_llm_time_ratio", AgentDimension.STEP_EFFICIENCY.value, EvalMethod.RULE,
              "sum(tool_duration) / sum(llm_duration)"),
    # Prompt Effect
    MetricDef("rag_injection_impact", AgentDimension.PROMPT_EFFECT.value, EvalMethod.COMPARE,
              "compare traces with/without RAG injection"),
    MetricDef("memory_injection_impact", AgentDimension.PROMPT_EFFECT.value, EvalMethod.COMPARE,
              "compare traces with/without memory injection"),
    MetricDef("prompt_version_ab", AgentDimension.PROMPT_EFFECT.value, EvalMethod.COMPARE,
              "compare traces with different system_prompt_hash"),
    # Answer Quality
    MetricDef("faithfulness", AgentDimension.ANSWER_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges if answer is faithful to retrieved content"),
    MetricDef("answer_relevance", AgentDimension.ANSWER_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges if answer is relevant to question"),
    MetricDef("answer_quality", AgentDimension.ANSWER_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges overall answer quality"),
]

# ── 多 Agent 协作 (4 dimensions) ──

COLLABORATION_METRICS: list[MetricDef] = [
    # Task Decomposition
    MetricDef("subtask_count", CollaborationDimension.TASK_DECOMPOSITION.value, EvalMethod.RULE,
              "count of tool.dispatch spans"),
    MetricDef("subtask_granularity", CollaborationDimension.TASK_DECOMPOSITION.value, EvalMethod.JUDGE,
              "LLM judges if subtask granularity is reasonable"),
    MetricDef("subtask_overlap", CollaborationDimension.TASK_DECOMPOSITION.value, EvalMethod.JUDGE,
              "LLM judges if subtasks overlap"),
    MetricDef("subtask_coverage", CollaborationDimension.TASK_DECOMPOSITION.value, EvalMethod.JUDGE,
              "LLM judges if subtasks cover user needs"),
    # Dispatch Efficiency
    MetricDef("dispatch_depth", CollaborationDimension.DISPATCH_EFFICIENCY.value, EvalMethod.RULE,
              "max(dispatch_depth) across all dispatch spans"),
    MetricDef("parallelism_degree", CollaborationDimension.DISPATCH_EFFICIENCY.value, EvalMethod.RULE,
              "max concurrent subagent runs"),
    MetricDef("sequential_bottleneck", CollaborationDimension.DISPATCH_EFFICIENCY.value, EvalMethod.RULE,
              "ratio of sequential dispatch time to total"),
    MetricDef("total_agent_runs", CollaborationDimension.DISPATCH_EFFICIENCY.value, EvalMethod.RULE,
              "total number of agent.run spans"),
    # Subagent Quality
    MetricDef("subagent_task_completion", CollaborationDimension.SUBAGENT_QUALITY.value, EvalMethod.RULE,
              "ratio of subagent end_turn completions"),
    MetricDef("subagent_output_relevance", CollaborationDimension.SUBAGENT_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges subagent output relevance"),
    MetricDef("subagent_token_waste", CollaborationDimension.SUBAGENT_QUALITY.value, EvalMethod.RULE,
              "tokens spent on subagents that produced no useful output"),
    # Aggregation Quality
    MetricDef("aggregation_fidelity", CollaborationDimension.AGGREGATION_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges if final answer integrates subagent outputs"),
    MetricDef("information_loss", CollaborationDimension.AGGREGATION_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges proportion of subagent output lost in aggregation"),
    MetricDef("redundancy_in_final_output", CollaborationDimension.AGGREGATION_QUALITY.value, EvalMethod.JUDGE,
              "LLM judges redundancy in final output"),
]

ALL_METRICS: list[MetricDef] = AGENT_METRICS + COLLABORATION_METRICS

# Quick lookup by name
METRIC_BY_NAME: dict[str, MetricDef] = {m.name: m for m in ALL_METRICS}
