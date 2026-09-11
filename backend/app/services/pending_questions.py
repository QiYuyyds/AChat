"""ask_user wait store.

Port of src/server/pending-questions.ts. Agent calls ask_user → register a
pending question → emit ``ask_user.pending`` → frontend dialog → user answers →
:meth:`answer` wakes the awaiting tool. Module-level singleton, in-memory.
The shared entry-map / resolver skeleton lives in
:mod:`app.services.pending_store_base`.
"""

from __future__ import annotations

from collections.abc import Callable

from app.schemas.dispatch import AskUserAnswer, AskUserQuestionItem, PendingQuestion
from app.schemas.events import AskUserPendingEvent, AskUserResolvedEvent
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase
from app.utils.clock import now_ms
from app.utils.ids import new_pending_question_id

# answers map (question text -> AskUserAnswer) or None on cancel
QuestionResolver = Callable[[dict[str, AskUserAnswer] | None], None]


class PendingQuestionsStore(PendingStoreBase):
    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        questions: list[AskUserQuestionItem],
        user_id: str | None = None,
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
        self.register_entry(
            BasePendingEntry(payload=question, user_id=user_id),
            AskUserPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_question=question,
            ),
        )
        return question

    def answer(self, pending_id: str, answers: dict[str, AskUserAnswer]) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        self._finalize(
            pending_id,
            resolver_payload=answers,
            resolved_event=AskUserResolvedEvent(
                conversation_id=entry.payload.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                answered=True,
            ),
        )
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as None without emitting an SSE event."""
        super().cancel(pending_id, resolver_payload=None)


pending_questions = PendingQuestionsStore()
