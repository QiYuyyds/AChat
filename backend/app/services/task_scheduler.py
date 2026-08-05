"""TaskSchedulerService — asyncio background scheduler for the global task pool.

Periodically scans for ``todo`` tasks and dispatches them to Agents via
``run_with_args`` → ``execute_run`` → ``run_agent_loop(mode='solo')``.

Single-instance per process. Start/stop is user-scoped.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.db.models import Task
from app.services import task_service

logger = logging.getLogger(__name__)


# Task tools injected into every dispatched agent so it can report
# completion / progress. These are opt-in tools normally, but the
# scheduler always injects them because the task workflow requires them.
_TASK_TOOLS: tuple[str, ...] = (
    "task_complete",
    "task_comment",
    "task_list",
    "task_get",
    "task_move",
)


def build_task_prompt(task: Task) -> str:
    """Build the trigger prompt for a dispatched task."""
    labels_str = ", ".join(task.labels) if task.labels else "无"
    if task.workspace_mode == "local" and task.workspace_path:
        workspace_desc = f"本地项目目录 {task.workspace_path}"
    else:
        workspace_desc = "沙箱模式（自动创建临时工作目录）"

    return f"""你正在执行一个全局任务池中的任务。

## 任务信息
- 标题：{task.title}
- 描述：{task.description or '（无详细描述）'}
- 优先级：{task.priority}
- 标签：{labels_str}
- 工作目录：{workspace_desc}

## 工作流程
1. 先用 create_plan 拆解任务步骤
2. 按步骤执行，每完成一步用 plan_step 更新状态
3. 全部完成后，调用 task_complete(taskId="{task.id}", ifVersion={task.version}, summary="<完成摘要>")

## 重要约束
- 你必须调用 task_complete 来标记任务完成，不要调用 task_move 到 done 状态
- task_complete 会将任务移到 in_review 状态，由用户评审后决定是否接受
- 如果遇到无法继续的阻塞，调用 task_move(taskId="{task.id}", status="blocked", ifVersion={task.version}, reason="<阻塞原因>")
- 你可以使用 task_comment 添加评论来记录进度
- 注意：task_complete 的 ifVersion 参数必须使用当前任务版本号。如果遇到版本冲突，先用 task_get 获取最新版本再重试
"""


@dataclass
class _SchedulerState:
    """Per-user scheduler state."""

    user_id: str
    agent_id: str | None
    interval_seconds: int
    max_concurrent: int
    task: asyncio.Task[None] | None = None
    active_count: int = 0
    running: bool = False


class TaskSchedulerService:
    """Singleton background scheduler."""

    _instance: TaskSchedulerService | None = None

    @classmethod
    def get_instance(cls) -> TaskSchedulerService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._states: dict[str, _SchedulerState] = {}

    async def start(
        self,
        *,
        user_id: str,
        agent_id: str | None,
        interval_seconds: int = 300,
        max_concurrent: int = 3,
    ) -> None:
        existing = self._states.get(user_id)
        if existing and existing.task and not existing.task.done():
            return  # Already running

        state = _SchedulerState(
            user_id=user_id,
            agent_id=agent_id,
            interval_seconds=interval_seconds,
            max_concurrent=max_concurrent,
            running=True,
        )
        state.task = asyncio.create_task(self._run_loop(state))
        self._states[user_id] = state

        pending = await task_service.count_todo_tasks(user_id)
        task_service.publish_scheduler_status(
            user_id, running=True, pending_count=pending, active_count=0
        )
        logger.info(
            "[TaskScheduler] Started for user=%s agent=%s interval=%ds",
            user_id,
            agent_id,
            interval_seconds,
        )

    def stop(self, user_id: str) -> None:
        state = self._states.get(user_id)
        if state is None:
            return
        state.running = False
        if state.task and not state.task.done():
            state.task.cancel()
        logger.info("[TaskScheduler] Stopped for user=%s", user_id)

    def get_status(self, user_id: str) -> dict[str, Any]:
        state = self._states.get(user_id)
        if state is None or not state.running:
            return {
                "running": False,
                "pendingCount": 0,
                "activeCount": 0,
            }
        return {
            "running": True,
            "pendingCount": 0,
            "activeCount": state.active_count,
        }

    async def _run_loop(self, state: _SchedulerState) -> None:
        try:
            while state.running:
                try:
                    await self._scan_and_dispatch(state)
                except Exception:
                    logger.exception("[TaskScheduler] Error in scan cycle")
                await asyncio.sleep(state.interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            state.running = False
            task_service.publish_scheduler_status(
                state.user_id,
                running=False,
                pending_count=0,
                active_count=0,
            )

    async def _scan_and_dispatch(self, state: _SchedulerState) -> None:
        if state.active_count >= state.max_concurrent:
            return

        tasks = await task_service.get_dispatchable_tasks(state.user_id)
        pending = len(tasks)
        for task in tasks:
            if state.active_count >= state.max_concurrent:
                break
            state.active_count += 1
            task_service.publish_scheduler_status(
                state.user_id,
                running=True,
                pending_count=pending,
                active_count=state.active_count,
            )
            asyncio.create_task(self._dispatch_and_track(state, task))

    async def _dispatch_and_track(self, state: _SchedulerState, task: Task) -> None:
        try:
            await self._dispatch_task(state, task)
        except Exception:
            logger.exception("[TaskScheduler] Failed to dispatch task %s", task.id)
            try:
                await task_service.rollback_dispatch(
                    state.user_id, task.id, if_version=task.version
                )
            except Exception:
                logger.exception(
                    "[TaskScheduler] Failed to rollback task %s", task.id
                )
        finally:
            state.active_count -= 1
            pending = await task_service.count_todo_tasks(state.user_id)
            task_service.publish_scheduler_status(
                state.user_id,
                running=state.running,
                pending_count=pending,
                active_count=state.active_count,
            )

    async def _dispatch_task(self, state: _SchedulerState, task: Task) -> None:
        from app.db.engine import get_local_db
        from app.db.models import Message
        from app.infra.cache_helpers import get_agent_cached
        from app.schemas.events import MessageAddedEvent, MessageRecord
        from app.services.agent_runner import RunArgs, run_with_args
        from app.services.conversation_service import create_conversation
        from app.services.event_bus import event_bus
        from app.utils.clock import now_ms
        from app.utils.ids import new_message_id

        agent_id = state.agent_id or task.assignee_agent_id
        if not agent_id:
            logger.warning(
                "[TaskScheduler] No agent for task %s, skipping", task.id
            )
            return

        # Load agent to inject task tools into the dispatch run.
        # Task tools (task_complete, etc.) are opt-in and may not be in the
        # agent's tool_names, but the task workflow requires them.
        agent = await get_agent_cached(agent_id)
        if not agent:
            logger.warning(
                "[TaskScheduler] Agent %s not found for task %s",
                agent_id, task.id,
            )
            return

        agent_tool_names = agent.tool_names_list
        override_tools = list(dict.fromkeys(
            list(agent_tool_names) + list(_TASK_TOOLS)
        ))

        bound_path = None
        if task.workspace_mode == "local" and task.workspace_path:
            bound_path = task.workspace_path

        conv = await create_conversation(
            mode="single",
            agent_ids=[agent_id],
            title=f"任务: {task.title[:60]}",
            bound_path=bound_path,
            user_id=state.user_id,
        )

        prompt = build_task_prompt(task)
        now = now_ms()
        message_id = new_message_id()
        parts = [{"type": "text", "content": prompt}]

        async with get_local_db() as db:
            msg = Message(
                id=message_id,
                conversation_id=conv.id,
                role="user",
                status="complete",
                created_at=now,
            )
            msg.parts_list = parts
            msg.mentioned_agent_ids_list = []
            db.add(msg)
            await db.commit()

        event_bus.publish(
            MessageAddedEvent(
                conversation_id=conv.id,
                timestamp=now,
                message=MessageRecord(
                    id=message_id,
                    conversation_id=conv.id,
                    role="user",
                    agent_id=None,
                    parts=parts,
                    status="complete",
                    parent_message_id=None,
                    mentioned_agent_ids=[],
                    run_id=None,
                    usage=None,
                    created_at=now,
                ),
            ),
            user_id=state.user_id,
        )

        await task_service.bind_conversation(
            state.user_id,
            task.id,
            conversation_id=conv.id,
            agent_id=agent_id,
            if_version=task.version,
        )

        args = RunArgs(
            agent_id=agent_id,
            conversation_id=conv.id,
            trigger_message_id=message_id,
            override_tool_names=override_tools,
            user_id=state.user_id,
        )

        _run_id, run_task, _cancel_event = run_with_args(args)
        result = await run_task

        if result.status == "failed":
            task_row = await task_service.get_task(state.user_id, task.id)
            if task_row and task_row.status == "in_progress":
                await task_service.rollback_dispatch(
                    state.user_id,
                    task.id,
                    if_version=task_row.version,
                )
        elif result.status == "complete":
            # Auto-complete: if the agent didn't call task_complete during its
            # run, transition the task to in_review so it doesn't get stuck.
            task_row = await task_service.get_task(state.user_id, task.id)
            if task_row and task_row.status == "in_progress":
                try:
                    await task_service.complete_task(
                        state.user_id,
                        task.id,
                        if_version=task_row.version,
                        summary="Agent 运行已结束（自动完成）",
                        author_type="agent",
                        author_id=agent_id,
                        author_name=agent.name,
                    )
                    logger.info(
                        "[TaskScheduler] Auto-completed task %s after run finished",
                        task.id,
                    )
                except Exception:
                    logger.exception(
                        "[TaskScheduler] Failed to auto-complete task %s",
                        task.id,
                    )
