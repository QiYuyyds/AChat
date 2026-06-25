"""ask_user wait store.

Port of src/server/pending-questions.ts. Agent calls ask_user → register a
pending question → emit ``ask_user.pending`` → frontend dialog → user answers →
:meth:`answer` wakes the awaiting tool. Module-level singleton, in-memory.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.dispatch import AskUserAnswer, AskUserQuestionItem, PendingQuestion
from app.schemas.events import AskUserPendingEvent, AskUserResolvedEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_pending_question_id

# answers map (question text -> AskUserAnswer) or None on cancel
QuestionResolver = Callable[[dict[str, AskUserAnswer] | None], None]


@dataclass
class _PendingEntry:
    question: PendingQuestion
    resolver: QuestionResolver | None = field(default=None)


class PendingQuestionsStore:
    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        questions: list[AskUserQuestionItem],
    ) -> PendingQuestion:
        created_at = now_ms()
        question = PendingQuestion(
            id=new_pending_question_id(),
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            questions=questions,
            created_at=created_at,
        )
        self._map[question.id] = _PendingEntry(question=question)

        event_bus.publish(
            AskUserPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_question=question,
            )
        )
        return question

    def attach_resolver(self, pending_id: str, resolver: QuestionResolver) -> None:
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingQuestion | None:
        entry = self._map.get(pending_id)
        return entry.question if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingQuestion]:
        questions = [
            e.question
            for e in self._map.values()
            if e.question.conversation_id == conversation_id
        ]
        questions.sort(key=lambda q: q.created_at)
        return questions

    def answer(self, pending_id: str, answers: dict[str, AskUserAnswer]) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if entry.resolver is not None:
            entry.resolver(answers)
        del self._map[pending_id]
        event_bus.publish(
            AskUserResolvedEvent(
                conversation_id=entry.question.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                answered=True,
            )
        )
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as None without emitting an SSE event."""
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver(None)
        del self._map[pending_id]


pending_questions = PendingQuestionsStore()
