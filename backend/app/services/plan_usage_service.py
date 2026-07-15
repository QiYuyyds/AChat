"""Plan usage statistics aggregation.

Computes aggregated plan usage metrics from agent_runs.usage JSONB data
for the plan-usage/stats API endpoint.
"""

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import AgentRun, Conversation


async def get_plan_usage_stats(user_id: str | None = None) -> dict:
    """Aggregate plan usage statistics across runs.

    Args:
        user_id: If provided, scope stats to this user's runs (via conversation.user_id).
                 If None, return global stats.

    Returns a camelCase wire dict matching the PlanUsageStatsResponse schema.
    """
    async with get_db() as db:
        # Build base query: runs with non-null usage
        stmt = select(AgentRun).where(AgentRun.usage.is_not(None))
        if user_id is not None:
            stmt = stmt.join(
                Conversation, AgentRun.conversation_id == Conversation.id
            ).where(Conversation.user_id == user_id)

        runs = (await db.execute(stmt)).scalars().all()

        total_runs = len(runs)
        with_plan = 0
        without_plan = 0
        complexity_dist: dict[str, int] = {"simple": 0, "moderate": 0, "complex": 0}
        step_counts_by_complexity: dict[str, list[int]] = {
            "simple": [],
            "moderate": [],
            "complex": [],
        }
        completed_by_complexity: dict[str, list[float]] = {
            "simple": [],
            "moderate": [],
            "complex": [],
        }
        total_added_steps = 0
        plan_run_count = 0

        for run in runs:
            usage = run.usage_dict
            if not usage:
                without_plan += 1
                continue

            plan = usage.get("plan")
            if not plan or not plan.get("created"):
                without_plan += 1
                continue

            with_plan += 1
            plan_run_count += 1
            complexity = plan.get("complexity", "moderate")
            step_count = plan.get("stepCount", 0)
            completed_steps = plan.get("completedSteps", 0)
            added_steps = plan.get("addedStepsCount", 0)

            # Complexity distribution
            if complexity in complexity_dist:
                complexity_dist[complexity] += 1

            # Step counts for avg calculation
            if complexity in step_counts_by_complexity:
                step_counts_by_complexity[complexity].append(step_count)

            # Completion rate per complexity
            if complexity in completed_by_complexity and step_count > 0:
                completed_by_complexity[complexity].append(
                    completed_steps / step_count
                )

            total_added_steps += added_steps

        # Compute averages
        avg_step_count: dict[str, float] = {}
        for c in ("simple", "moderate", "complex"):
            counts = step_counts_by_complexity[c]
            avg_step_count[c] = round(sum(counts) / len(counts), 1) if counts else 0.0

        completion_rate: dict[str, float] = {}
        for c in ("simple", "moderate", "complex"):
            rates = completed_by_complexity[c]
            completion_rate[c] = round(sum(rates) / len(rates), 2) if rates else 0.0

        avg_added_steps = round(total_added_steps / plan_run_count, 1) if plan_run_count else 0.0

        return {
            "totalRuns": total_runs,
            "withPlan": with_plan,
            "withoutPlan": without_plan,
            "complexityDistribution": complexity_dist,
            "avgStepCount": avg_step_count,
            "completionRate": completion_rate,
            "avgAddedSteps": avg_added_steps,
        }
