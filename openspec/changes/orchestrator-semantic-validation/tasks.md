## 1. Advisory evidence collector refactor

- [x] 1.1 Define `TaskEvidenceSummary` dataclass in `task_result_report.py` with fields: `advisory_issues: list[str]`, `has_report: bool`, `report_status: str | None`, `evidence: RunToolEvidence`.
- [x] 1.2 Refactor `evaluate_task_result_report` to return `TaskEvidenceSummary` instead of `TaskResultReportEvaluation`. Keep ② (`report.status != "complete"`) and ④ (`acceptanceResults` has `passed=false`) as hard rules that set `report_status` for the caller to check. Convert ③⑤⑥⑦⑧⑨ from fail-fast returns to `advisory_issues.append(...)` collection.
- [x] 1.3 Update all consumers of `TaskResultReportEvaluation` in `orchestrator.py` (`_evaluate_child_task_result`) to handle the new return type: check `has_report` and `report_status` for hard fails, then pass `advisory_issues` to the LLM evaluator.
- [x] 1.4 Update tests in `tests/test_task_result_evaluate.py`: change all assertions from `result.ok` / `result.error` to `summary.advisory_issues` / `summary.report_status`.

## 2. Orchestrator LLM evaluation

- [x] 2.1 Add `_evaluate_with_llm(task, report, summary)` method in `orchestrator.py`. Construct a single-turn prompt with task contract + agent report + advisory issues, instruct the LLM to return `{"pass": bool, "feedback": str}` JSON. Call `consume_stream` with no tools (single-turn, JSON output).
- [x] 2.2 Add evaluation prompt template in `orchestrator_prompts.py` (`render_evaluation_prompt`): includes task description, acceptance criteria, agent's report summary, and advisory issues list. Instructions: "Judge whether the task is essentially complete. Advisory issues are context, not automatic failures. Return JSON."
- [x] 2.3 Wire `_evaluate_with_llm` into `_evaluate_child_task_result`: after `evaluate_task_result_report` returns, if `has_report` and `report_status == "complete"`, call `_evaluate_with_llm`. If LLM returns `pass=false`, set task status to `failed` with `feedback` as the error message (used as continuation prompt by harness loop).
- [x] 2.4 Skip LLM call when `advisory_issues` is empty: if no issues and `report_status == "complete"`, directly return `complete` without LLM call (latency optimization).
- [x] 2.5 Add test with mock adapter: task with advisory issues → LLM called → returns `pass=true` → task `complete`; LLM returns `pass=false` with feedback → task `failed` with feedback as error.

## 3. Remove hardcoded auto-append

- [x] 3.1 In `dispatch_plan.py` `normalize_task_contract`, remove the `has_build_toolchain` conditional append of `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` and `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE`. The function now only ensures the `project` expected output for code tasks.
- [x] 3.2 Remove or deprecate `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` and `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE` constants (keep as dead code with deprecation comment, or delete if no other references).
- [x] 3.3 Update `compile_and_validate_dispatch_plan` and its call sites: remove `has_build_toolchain` parameter threading (no longer needed since `normalize_task_contract` doesn't append toolchain-dependent criteria).
- [x] 3.4 Update tests in `tests/test_dispatch_plan.py`: remove assertions about auto-appended criteria; verify `normalize_task_contract` still ensures `project` output but doesn't append verification criteria.

## 4. Review agent replan trigger

- [x] 4.1 In `orchestrator.py` `_run_dispatch_stage`, after a round completes, check if any review task (a task whose `dependsOn` includes a completed implementation task) has `status="failed"`. If so, trigger replan even if the implementation task itself passed.
- [x] 4.2 In the replan prompt construction, include the review task's `task_report` feedback (from `report_task_result` summary/blockers) so the Orchestrator LLM can create a remediation plan targeting the implementation task.
- [x] 4.3 Add test: DAG with `t1: implementation (complete)` + `t2: review depends on t1 (failed with feedback)` → replan triggered → new round with `t1'` continuation prompt containing review feedback.

## 5. Plan prompt constraint guidance

- [x] 5.1 In `orchestrator_prompts.py` plan prompt (the `_run_plan_stage` prompt), add guidance: "For each task, include explicit constraints in the task description that the sub-agent must follow (e.g., cite specific sources, add code comments, limit output format). These constraints are advisory — the sub-agent retains autonomous decision-making."
- [x] 5.2 Add example in the plan prompt showing a task description with constraints: e.g., "实现贪吃蛇游戏。约束：代码需包含关键逻辑注释，HTML文件需可直接在浏览器打开运行。"

## 6. Integration verification

- [x] 6.1 Unit test: `evaluate_task_result_report` returns `TaskEvidenceSummary` with correct `advisory_issues` for: failed command, missing verification, unmatched criteria, missing target path, missing required command, missing required evidence.
- [x] 6.2 Unit test: `_evaluate_with_llm` with mock adapter returns correct pass/fail + feedback for: advisory issues present + LLM says pass; advisory issues present + LLM says fail; no advisory issues → LLM not called.
- [x] 6.3 Unit test: review task failed → replan triggered with review feedback in continuation prompt.
- [x] 6.4 Regression check: `pytest tests/test_orchestrator.py tests/test_dispatch_plan.py tests/test_task_result_evaluate.py` passes.
- [ ] 6.5 End-to-end manual test: group chat with Orchestrator + custom agent; send "生成一个贪吃蛇HTML小游戏"; verify child task completes without hardcoded gate blocking; verify advisory issues are collected and LLM evaluation passes for a reasonable HTML output.
