"""Tests for the context-usage-breakdown capability.

Covers:
- 4.1: _measure_context_breakdown classification (skills split out of the
  system prompt via system_prompt_sections, registry vs MCP tool schemas,
  messages covering history + user, missing sections degrade to skills=0)
- 4.2: _rescale_context_breakdown invariants (six categories sum to the
  turn's real inputTokens, other residual >= 0, all-zero payload / zero
  inputTokens produce no NaN or division by zero)
- Contract: RunUsage.contextBreakdown serializes camelCase, backward-compatible
  with old persisted JSON, and CLI-style runs carry no breakdown (None).
"""

import json

from app.adapters.custom_adapter import (
    _BREAKDOWN_MEASURED_KEYS,
    _measure_context_breakdown,
    _rescale_context_breakdown,
)
from app.schemas.messages import ContextBreakdown, RunUsage
from app.utils.model_registry import estimate_tokens


# 4.1 ── measurement helper classification ────────────────────────────────────


class TestMeasureContextBreakdown:
    def test_splits_skills_from_system_prompt(self):
        base = "A" * 400
        skill_block = "S" * 200
        full = base + "\n\n" + skill_block
        raw = _measure_context_breakdown(
            [{"role": "system", "content": full}, {"role": "user", "content": "hi"}],
            registry_tool_schemas=[],
            system_prompt_sections={"base": len(base), "skills": len(skill_block)},
        )
        assert raw["skills"] == estimate_tokens(skill_block)
        assert raw["system_prompt"] == estimate_tokens(full) - estimate_tokens(skill_block)

    def test_sections_missing_degrades_to_skills_zero(self):
        raw = _measure_context_breakdown(
            [{"role": "system", "content": "sys prompt"}, {"role": "user", "content": "hi"}],
            registry_tool_schemas=[],
            system_prompt_sections=None,
        )
        assert raw["skills"] == 0
        assert raw["system_prompt"] == estimate_tokens("sys prompt")

    def test_sections_oversized_clamped_no_negative(self):
        """Sections longer than the actual system message clamp at its length."""
        raw = _measure_context_breakdown(
            [{"role": "system", "content": "sys"}],
            registry_tool_schemas=[],
            system_prompt_sections={"skills": 10_000},
        )
        assert raw["skills"] == estimate_tokens("sys")
        assert raw["system_prompt"] == 0

    def test_registry_and_mcp_tools_split_by_origin(self):
        registry = [{
            "type": "function",
            "function": {"name": "fs_read", "description": "read a file", "parameters": {}},
        }]
        mcp = [{
            "type": "function",
            "function": {"name": "mcp__srv__tool", "description": "external", "parameters": {}},
        }]
        raw = _measure_context_breakdown(
            [{"role": "user", "content": "hi"}],
            registry_tool_schemas=registry,
            mcp_tool_schemas=mcp,
        )
        assert raw["system_tools"] == estimate_tokens(json.dumps(registry, ensure_ascii=False))
        assert raw["mcp_tools"] == estimate_tokens(json.dumps(mcp, ensure_ascii=False))

    def test_messages_covers_history_and_current_user(self):
        system = {"role": "system", "content": "sys"}
        history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        current = {"role": "user", "content": "new question"}
        raw = _measure_context_breakdown(
            [system, *history, current],
            registry_tool_schemas=[],
        )
        assert raw["messages"] == estimate_tokens(
            json.dumps([*history, current], ensure_ascii=False)
        )
        assert raw["system_prompt"] == estimate_tokens("sys")
        assert raw["system_tools"] == 0
        assert raw["mcp_tools"] == 0

    def test_extra_system_messages_count_as_system_prompt(self):
        """Injected system hints (hooks / soft-inject) stay in the system category."""
        raw = _measure_context_breakdown(
            [
                {"role": "system", "content": "main prompt"},
                {"role": "system", "content": "hint"},
                {"role": "user", "content": "hi"},
            ],
            registry_tool_schemas=[],
        )
        assert raw["system_prompt"] == estimate_tokens("main prompt") + estimate_tokens("hint")


# 4.2 ── rescale invariants ───────────────────────────────────────────────────


class TestRescaleContextBreakdown:
    def test_categories_sum_to_real_input_tokens(self):
        raw = {"system_prompt": 100, "skills": 10, "system_tools": 50, "mcp_tools": 40, "messages": 300}
        scaled = _rescale_context_breakdown(raw, 1000)
        assert sum(scaled.values()) == 1000
        assert scaled["other"] >= 0
        # proportions preserved (±1 token rounding)
        assert abs(scaled["messages"] - 600) <= 1
        assert abs(scaled["skills"] - 20) <= 1

    def test_other_residual_absorbs_rounding(self):
        """Rounding down leaves the remainder in other; sum stays exact."""
        scaled = _rescale_context_breakdown({"system_prompt": 1, "messages": 1}, 1)
        assert sum(scaled.values()) == 1
        assert scaled["other"] >= 0

    def test_all_zero_payload_no_division_by_zero(self):
        raw = dict.fromkeys(_BREAKDOWN_MEASURED_KEYS, 0)
        scaled = _rescale_context_breakdown(raw, 500)
        assert sum(scaled.values()) == 500
        assert scaled["other"] == 500
        assert all(scaled[k] == 0 for k in _BREAKDOWN_MEASURED_KEYS)

    def test_zero_input_tokens_yields_all_zeros(self):
        scaled = _rescale_context_breakdown({"messages": 100}, 0)
        assert sum(scaled.values()) == 0

    def test_negative_or_garbage_raw_values_ignored(self):
        scaled = _rescale_context_breakdown(
            {"system_prompt": -5, "messages": "bad", "skills": 100}, 400
        )
        assert sum(scaled.values()) == 400
        assert scaled["skills"] == 400


# ── RunUsage contract ────────────────────────────────────────────────────────


class TestRunUsageContextBreakdownContract:
    def test_serializes_camel_case(self):
        ru = RunUsage(
            input_tokens=100,
            output_tokens=20,
            cache_creation_tokens=0,
            cache_read_tokens=50,
            last_input_tokens=80,
            context_breakdown=ContextBreakdown(
                system_prompt=10, skills=2, system_tools=8, mcp_tools=5,
                messages=50, other=5,
            ),
        )
        data = ru.model_dump(by_alias=True)
        bd = data["contextBreakdown"]
        assert bd["systemPrompt"] == 10
        assert bd["systemTools"] == 8
        assert bd["mcpTools"] == 5
        assert sum(bd.values()) == 80

    def test_old_persisted_json_without_field_deserializes_to_none(self):
        old_json = {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 50,
            "lastInputTokens": 30,
        }
        ru = RunUsage.model_validate(old_json)
        assert ru.context_breakdown is None

    def test_cli_style_run_usage_has_no_breakdown(self):
        """CLI adapters construct RunUsage without composition — no fake data."""
        ru = RunUsage(
            input_tokens=12, output_tokens=7,
            cache_creation_tokens=0, cache_read_tokens=0,
        )
        assert ru.context_breakdown is None
        assert ru.model_dump(by_alias=True)["contextBreakdown"] is None
