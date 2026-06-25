"""ask_user tool — structured multiple-choice questions to the user.

Port of src/server/tools/ask-user.ts. Schema aligns with the Anthropic SDK's
AskUserQuestion (1-4 questions × 2-4 options each). Registers a pending question,
emits ``ask_user.pending``, and waits for the answer (or run abort).

Returns ``{ answers: { <question>: "label1, label2" | "...; note: free text" } }``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.dispatch import AskUserAnswer, AskUserOption, AskUserQuestionItem
from app.services.pending_questions import pending_questions
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.approval import await_pending_decision


class _Option(BaseModel):
    label: str = Field(min_length=1)
    description: str = ""
    preview: str | None = None


class _Question(BaseModel):
    question: str = Field(min_length=1)
    header: str = Field(min_length=1, max_length=40)
    options: list[_Option] = Field(min_length=2, max_length=4)
    multi_select: bool = Field(default=False, alias="multiSelect")
    model_config = ConfigDict(populate_by_name=True)


class _Args(BaseModel):
    questions: list[_Question] = Field(min_length=1, max_length=4)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "description": "Questions to ask (1-4)",
            "items": {
                "type": "object",
                "required": ["question", "header", "options"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Full question text (end with ?)",
                    },
                    "header": {"type": "string", "description": "Short chip label (≤12 chars)"},
                    "multiSelect": {
                        "type": "boolean",
                        "description": "true = user can pick multiple options",
                    },
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "required": ["label"],
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "Short (1-5 words) shown in button",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Explanation / trade-off note",
                                },
                                "preview": {
                                    "type": "string",
                                    "description": (
                                        "Optional code/text preview to show next to "
                                        "the option"
                                    ),
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    questions = [
        AskUserQuestionItem(
            question=q.question,
            header=q.header,
            options=[
                AskUserOption(label=o.label, description=o.description, preview=o.preview)
                for o in q.options
            ],
            multi_select=q.multi_select,
        )
        for q in parsed.questions
    ]

    pending = pending_questions.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        questions=questions,
    )

    decision: dict[str, AskUserAnswer] | None = await await_pending_decision(
        attach_resolver=lambda r: pending_questions.attach_resolver(pending.id, r),
        cancel=lambda: pending_questions.cancel(pending.id),
        cancel_event=ctx.cancel_event,
        cancelled_value=None,
    )

    if not decision:
        return err("User did not answer the question (aborted)")

    formatted: dict[str, str] = {}
    for q in parsed.questions:
        a = decision.get(q.question)
        if not a:
            formatted[q.question] = "(no answer)"
            continue
        parts: list[str] = []
        if a.selected_labels:
            parts.append(", ".join(a.selected_labels))
        if a.freeform_note and a.freeform_note.strip():
            parts.append(f"note: {a.freeform_note.strip()}")
        formatted[q.question] = " ; ".join(parts) or "(empty)"

    return ok({"answers": formatted})


ask_user_tool = ToolDef(
    name="ask_user",
    description=(
        "Ask the user one or more structured multiple-choice questions with 2-4 "
        "options each. Use when there is a clear set of choices the user should pick "
        "from (vs. open-ended questions, where you should just ask in text). Each "
        "option carries a short label + a description + optional preview content "
        "(code snippet, mockup) for the dropdown UI. Returns the user's chosen labels "
        "(and any free-form note they added)."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
