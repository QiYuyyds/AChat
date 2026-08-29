"""
Model-based grader — LLM-as-Judge scoring.

Sends the trial transcript and a rubric to an LLM, which returns
a score between 0 and 1.

Config schema:
    {
        "rubric": "The response must contain...",
        "dimensions": ["correctness", "completeness"],
        "threshold": 0.7,
        "model": "gpt-4o-mini",  # optional
    }

Requires either:
- A configured llm_fn callback, or
- An API key in the environment (OPENAI_API_KEY, etc.)
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from eval_harness.core.contract import EvalContext, Grader
from eval_harness.core.types import GraderResult, GraderType, EvalTask, TrialResult


# Type alias for LLM function: (system_prompt, user_message) -> str
LLMFn = Callable[[str, str], str]


class ModelBasedGrader:
    """LLM-as-Judge 评分器"""

    name = "model_based"

    def __init__(self, llm_fn: LLMFn | None = None):
        """
        Args:
            llm_fn: (system_prompt, user_message) → response text.
                    If None, uses OpenAI API with OPENAI_API_KEY.
        """
        self._llm_fn = llm_fn

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        rubric = config.get("rubric", "")
        dimensions = config.get("dimensions", ["quality"])
        threshold = config.get("threshold", 0.7)

        # Build judge prompt
        prompt = self._build_prompt(trial, rubric, dimensions)

        # Call LLM
        llm_fn = self._llm_fn or self._default_llm_fn(config)
        try:
            raw = llm_fn("You are an evaluation expert.", prompt)
        except Exception as e:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.MODEL,
                score=0.0,
                passed=False,
                explanation=f"LLM call failed: {e}",
            )

        # Parse scores
        scores = self._parse_scores(raw, dimensions)
        avg_score = sum(scores.values()) / len(scores) if scores else 0.0

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.MODEL,
            score=avg_score,
            passed=avg_score >= threshold,
            explanation=f"LLM Judge scores: {scores}",
            details={"dimensions": scores, "raw_response": raw},
        )

    def _build_prompt(
        self,
        trial: TrialResult,
        rubric: str,
        dimensions: list[str],
    ) -> str:
        """构建 judge prompt"""
        input_msg = trial.transcript[0] if trial.transcript else "N/A"
        output_msg = trial.transcript[-1] if trial.transcript else "N/A"

        # 提取工具调用摘要
        tools_used = list(set(
            msg.get("tool_name", "")
            for msg in trial.transcript
            if msg.get("role") == "tool_call"
        ))

        dims_json = ", ".join(f'"{d}": 0.0' for d in dimensions)

        return f"""请根据以下评分标准对 Agent 表现进行评分。

## 评分标准
{rubric}

## 评分维度
{", ".join(dimensions)}

## Agent 执行记录
- 输入: {input_msg}
- 输出: {output_msg}
- 使用的工具: {tools_used or "无"}

请以 JSON 格式返回各维度评分 (0.0-1.0):
```json
{{{dims_json}}}
```"""

    def _parse_scores(self, raw: str, dimensions: list[str]) -> dict[str, float]:
        """从 LLM 响应中解析分数"""
        scores: dict[str, float] = {}

        # 尝试提取 JSON
        try:
            # 查找 JSON 块
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = raw[json_start:json_end]
                parsed = json.loads(json_str)
                for dim in dimensions:
                    if dim in parsed:
                        try:
                            scores[dim] = float(parsed[dim])
                        except (ValueError, TypeError):
                            pass
        except json.JSONDecodeError:
            pass

        # 如果解析失败，给所有维度默认分
        if not scores:
            for dim in dimensions:
                scores[dim] = 0.5

        return scores

    def _default_llm_fn(self, config: dict[str, Any]) -> LLMFn:
        """创建默认的 LLM 调用函数"""
        model = config.get("model", "gpt-4o-mini")

        def call_llm(system: str, user: str) -> str:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.0,
                    max_tokens=500,
                )
                return resp.choices[0].message.content or ""
            except ImportError:
                raise RuntimeError(
                    "openai package not installed. "
                    "Install with: pip install openai"
                )
            except Exception as e:
                raise RuntimeError(f"OpenAI API call failed: {e}") from e

        return call_llm
