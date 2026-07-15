## 1. Backend — Plan Usage Statistics Collection

- [x] 1.1 In run-end handler (consume_stream or finalize), read plan_registry data for the current run and write `plan` stats into `agent_runs.usage` JSONB
- [x] 1.2 Ensure `plan` key is absent (not null) for runs without plans — only write when a plan was created

## 2. Backend — Plan Usage Stats API

- [x] 2.1 Create `GET /api/plan-usage/stats` endpoint in a new `backend/app/api/plan_usage.py` router
- [x] 2.2 Implement aggregation query: count runs with/without plans, complexity distribution, avg step count, completion rate, avg added steps
- [x] 2.3 Scope stats to `user_id` (regular users see own stats, admin sees global)
- [x] 2.4 Register the router in `backend/app/main.py`

## 3. Backend — Prompt Optimization

- [x] 3.1 Replace the current `_PLAN_SUFFIX` in `agent_loop.py` with optimized version:
  - Specific boundary conditions (DO: 3+ files, research+implement, user asks step-by-step; DON'T: single config, info question, one-line fix)
  - 2-3 few-shot judgment examples
  - Self-check reminder ("Does the user need to see my plan?")
- [x] 3.2 Similarly update coordinated mode plan guidance prompt (`_COORDINATED_PLAN_SUFFIX` if it exists from the coordinated change)

## 4. Verification

- [ ] 4.1 Run manual test: simple task ("fix a typo") → model should NOT call create_plan
- [ ] 4.2 Run manual test: complex task ("build a user auth system") → model SHOULD call create_plan
- [ ] 4.3 Verify plan usage stats appear in `agent_runs.usage` for plan runs
- [ ] 4.4 Verify `/api/plan-usage/stats` returns correct aggregated data
